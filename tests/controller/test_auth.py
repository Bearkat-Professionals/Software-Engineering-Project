"""Tests for sign-in, sessions and route protection (#90), through Flask's test client
with fake stores and a fake clock. No database, network or argon2.

    python -m unittest tests.controller.test_auth -v
"""
from __future__ import annotations

import os
import re
import unittest
from unittest import mock

from flask import Flask
from flask.testing import FlaskClient

from backend.controller.app_controller import Services, create_app
from backend.controller.auth import (ACCESS_RULE, IDLE_MAX_S, SESSION_MAX_S, check_secret_key,
                                     read_secret_key)
from backend.records import User
from backend.records.enums import UserRole
from backend.services.access_control.access_control_service import WRONG_LOGIN
from backend.services.access_control.permissions import Permission
from backend.services.scan.scan_session import ScanState, ScanStatusReport
from tests.controller.test_device_routes import FakeDeviceMaintenance, FakePickers
from tests.controller.test_api_routes import FakeDevices, FakeScanManager, FakeSessions
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeClock, FakeUsers

AUTH_LOG = "backend.controller.auth"
HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"
SAM = User(user_id=4, username="sam", password_hash=HASH, role=UserRole.EDITOR)
VAL = User(user_id=5, username="val", password_hash=HASH, role=UserRole.VIEWER)
ADA = User(user_id=6, username="ada", password_hash=HASH, role=UserRole.ADMIN)


class AuthTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.users = FakeUsers(SAM, VAL, ADA)
        self.clock = FakeClock()
        self.manager = FakeScanManager()
        self.app = self.make_app()
        self.client = self.app.test_client()

    def make_app(self, https: bool = False) -> Flask:
        services = Services(scan_manager=self.manager,
                            scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                            devices=FakeDevices(), users=self.users, access=FakeAccess(self.users),
                            reference=FakePickers(), device_maintenance=FakeDeviceMaintenance())
        return create_app(services, secret_key=SECRET_KEY, https=https, clock=self.clock)

    def sign_in(self, user: User, client: FlaskClient | None = None):
        return (client or self.client).post("/api/login", json={"username": user.username, "password": PASSWORD})

    def api_routes(self) -> list[tuple[str, str]]:
        """Every (method, URL) the app answers under /api/, with each <placeholder> filled in
        (a number for <int:...>, otherwise "x"), so the URL reaches the route's own checks."""
        return [(method, _example_url(rule.rule)) for rule in self.app.url_map.iter_rules()
                if rule.rule.startswith("/api/")
                for method in sorted(rule.methods - {"HEAD", "OPTIONS"})]


    def call(self, method: str, path: str, client: FlaskClient | None = None):
        return (client or self.client).open(path, method=method, json={} if method != "GET" else None)

def _example_url(rule: str) -> str:
    """'/api/reference/<kind>/<int:record_id>' -> '/api/reference/x/1'."""
    return re.sub(r"<(?:(\w+)(?:\([^)]*\))?:)?\w+>",
                  lambda match: "1" if match.group(1) == "int" else "x", rule)

class EveryRouteIsProtectedTests(AuthTestCase):
    def test_every_api_route_has_requires_or_public(self) -> None:
        for rule in self.app.url_map.iter_rules():
            if rule.rule.startswith("/api/"):
                with self.subTest(route=rule.rule):
                    view = self.app.view_functions[rule.endpoint]
                    self.assertIsNotNone(getattr(view, ACCESS_RULE, None),
                                         f"{rule.rule} needs @requires(...) or @public")

    def test_only_login_is_public(self) -> None:
        public = [rule.rule for rule in self.app.url_map.iter_rules()
                  if getattr(self.app.view_functions[rule.endpoint], ACCESS_RULE, None) == "public"]
        self.assertEqual(public, ["/api/login"])

    def test_signed_out_every_route_but_login_is_401(self) -> None:
        routes = [route for route in self.api_routes() if route != ("POST", "/api/login")]
        self.assertGreaterEqual(len(routes), 5)
        for method, path in routes:
            with self.subTest(route=f"{method} {path}"):
                with self.assertLogs(AUTH_LOG, level="INFO"):
                    self.assertEqual(self.call(method, path).status_code, 401)
        self.assertEqual(self.manager.calls, [])

    def test_the_report_and_export_routes_are_guarded_by_their_permission(self) -> None:     # #156
        expected = {"/api/scans": Permission.VIEW_DATA, "/api/reports/changes": Permission.VIEW_DATA,
                    "/api/export": Permission.EXPORT}
        rules = {rule.rule: self.app.view_functions[rule.endpoint] for rule in self.app.url_map.iter_rules()}
        for path, permission in expected.items():
            with self.subTest(route=path):
                self.assertEqual(getattr(rules[path], ACCESS_RULE, None), permission)
                with self.assertLogs(AUTH_LOG, level="INFO"):
                    self.assertEqual(self.client.get(path).status_code, 401)


class SignInTests(AuthTestCase):
    def test_sign_in_returns_the_user_and_their_permissions(self) -> None:
        response = self.sign_in(VAL)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["user"]["username"], "val")
        self.assertEqual(body["user"]["role"], "viewer")
        self.assertEqual(body["permissions"], ["EXPORT", "VIEW_DATA"])

    def test_wrong_password_is_401_and_sets_no_session(self) -> None:
        with self.assertLogs(AUTH_LOG, level="WARNING"):
            response = self.client.post("/api/login", json={"username": "val", "password": "nope"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": WRONG_LOGIN})
        with self.assertLogs(AUTH_LOG, level="INFO"):
            self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_missing_username_or_password_is_400(self) -> None:
        for body in ({}, {"username": "val"}, {"password": PASSWORD}, {"username": 5, "password": PASSWORD}, []):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/login", json=body).status_code, 400)

    def test_me_returns_the_signed_in_user(self) -> None:
        self.sign_in(SAM)
        body = self.client.get("/api/me").get_json()
        self.assertEqual(body["user"]["username"], "sam")
        self.assertIn("START_SCAN", body["permissions"])

    def test_cookie_is_httponly_and_samesite_strict(self) -> None:
        cookie = self.sign_in(VAL).headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)                  # plain HTTP until M7

    def test_cookie_is_secure_over_https(self) -> None:
        client = self.make_app(https=True).test_client()
        self.assertIn("Secure", self.sign_in(VAL, client).headers["Set-Cookie"])

    def test_signing_in_again_replaces_the_old_session(self) -> None:
        self.sign_in(VAL)
        old_cookie = self.client.get_cookie("session").value
        self.sign_in(SAM)                                    # same browser, a new sign-in
        self.assertNotEqual(self.client.get_cookie("session").value, old_cookie)

        replay = self.app.test_client()
        replay.set_cookie("session", old_cookie)
        with self.assertLogs(AUTH_LOG, level="INFO"):
            self.assertEqual(replay.get("/api/me").status_code, 401)    # session fixation


class SignOutAndTimeoutTests(AuthTestCase):
    def test_sign_out_without_a_body_is_415_and_stays_signed_in(self) -> None:
        """Every change must be JSON, sign-out too: the screens send {} (see the README)."""
        self.sign_in(VAL)
        response = self.client.post("/api/logout")
        self.assertEqual(response.status_code, 415)
        self.assertTrue(response.get_json()["error"])
        self.assertEqual(self.client.get("/api/me").status_code, 200)       # still signed in
        self.assertEqual(self.client.post("/api/logout", json={}).status_code, 204)

    def test_after_sign_out_the_old_cookie_is_401(self) -> None:
        self.sign_in(VAL)
        old_cookie = self.client.get_cookie("session").value
        self.assertEqual(self.client.post("/api/logout", json={}).status_code, 204)

        replay = self.app.test_client()
        replay.set_cookie("session", old_cookie)
        with self.assertLogs(AUTH_LOG, level="INFO"):
            self.assertEqual(replay.get("/api/devices").status_code, 401)

    def test_disabled_after_sign_in_is_401_on_the_next_request(self) -> None:
        self.sign_in(VAL)
        self.users.disable(VAL)
        with self.assertLogs(AUTH_LOG, level="INFO"):
            response = self.client.get("/api/devices")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "This account is disabled."})

        self.users.users[VAL.user_id] = VAL                  # turned back on: still signed out
        with self.assertLogs(AUTH_LOG, level="INFO"):
            self.assertEqual(self.client.get("/api/devices").status_code, 401)

    def test_31_minutes_idle_is_401(self) -> None:
        self.sign_in(VAL)
        self.clock.advance(31 * 60)
        with self.assertLogs(AUTH_LOG, level="INFO"):
            response = self.client.get("/api/devices")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Sign in again", response.get_json()["error"])

    def test_activity_keeps_the_session_alive(self) -> None:
        self.sign_in(VAL)
        for _ in range(6):                                   # a request every 25 minutes
            self.clock.advance(25 * 60)
            self.assertEqual(self.client.get("/api/devices").status_code, 200)

    def test_8_hours_ends_the_session_even_when_active(self) -> None:
        self.sign_in(VAL)
        for _ in range(int(SESSION_MAX_S // (20 * 60)) - 1):  # busy, every 20 minutes
            self.clock.advance(20 * 60)
            self.assertEqual(self.client.get("/api/devices").status_code, 200)
        self.clock.advance(20 * 60)
        with self.assertLogs(AUTH_LOG, level="INFO"):
            self.assertEqual(self.client.get("/api/devices").status_code, 401)

    def test_the_limits_are_30_minutes_and_8_hours(self) -> None:
        self.assertEqual((IDLE_MAX_S, SESSION_MAX_S), (30 * 60, 8 * 60 * 60))


class PermissionTests(AuthTestCase):
    def test_viewer_403_on_starting_a_scan_and_200_on_the_device_list(self) -> None:
        self.sign_in(VAL)
        with self.assertLogs(AUTH_LOG, level="WARNING"):                # out of role: a WARNING (AT-13)
            response = self.client.post("/api/scans", json={"scope": "192.168.1.0/24"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "Your role (viewer) can't start scans."})
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(self.client.get("/api/devices").status_code, 200)

    def test_scan_is_started_as_the_session_user_not_the_body(self) -> None:
        self.sign_in(SAM)
        self.client.post("/api/scans", json={"scope": "192.168.1.0/24", "user_id": ADA.user_id})
        self.assertEqual(self.manager.calls, [("192.168.1.0/24", SAM.user_id)])


class LeakTests(AuthTestCase):
    def test_no_response_contains_the_hash(self) -> None:
        for user in (VAL, SAM, ADA):
            with self.subTest(user=user.username):
                client = self.app.test_client()
                bodies = [self.sign_in(user, client).get_data(as_text=True)]
                for method, path in self.api_routes():
                    if path not in ("/api/login", "/api/logout"):
                        bodies.append(self.call(method, path, client).get_data(as_text=True))
                for body in bodies:
                    self.assertNotIn("password_hash", body)
                    self.assertNotIn("$argon2", body)

    def test_a_refused_request_is_logged_without_the_password(self) -> None:
        self.sign_in(VAL)
        with self.assertLogs(AUTH_LOG, level="WARNING") as logs:
            self.client.post("/api/scans", json={"scope": "192.168.1.0/24", "password": PASSWORD})
        self.assertEqual(len(logs.output), 1)
        for expected in ("val", "POST", "/api/scans", "START_SCAN"):
            self.assertIn(expected, logs.output[0])
        self.assertNotIn(PASSWORD, logs.output[0])
        self.assertNotIn(HASH, logs.output[0])

    def test_a_failed_sign_in_is_logged_without_the_password(self) -> None:
        guess = "a guess 12345"
        with self.assertLogs(AUTH_LOG, level="WARNING") as logs:
            self.client.post("/api/login", json={"username": "val", "password": guess})
        self.assertEqual(len(logs.output), 1)
        self.assertIn("val", logs.output[0])
        self.assertIn(WRONG_LOGIN, logs.output[0])
        self.assertNotIn(guess, logs.output[0])

    def test_signed_out_is_logged_at_info_not_warning(self) -> None:
        with self.assertNoLogs(AUTH_LOG, level="WARNING"):
            with self.assertLogs(AUTH_LOG, level="INFO"):
                self.assertEqual(self.client.get("/api/devices").status_code, 401)


class JsonOnlyTests(AuthTestCase):
    def test_form_encoded_post_is_415(self) -> None:
        self.sign_in(SAM)
        response = self.client.post("/api/scans", data={"scope": "192.168.1.0/24"})
        self.assertEqual(response.status_code, 415)
        self.assertTrue(response.get_json()["error"])
        self.assertEqual(self.manager.calls, [])

    def test_form_encoded_sign_in_is_415(self) -> None:
        response = self.client.post("/api/login", data={"username": "val", "password": PASSWORD})
        self.assertEqual(response.status_code, 415)

    def test_get_needs_no_json(self) -> None:
        self.sign_in(VAL)
        self.assertEqual(self.client.get("/api/devices").status_code, 200)


class SecretKeyTests(unittest.TestCase):
    def test_a_long_enough_key_is_accepted(self) -> None:
        self.assertEqual(check_secret_key("a" * 32), "a" * 32)
        with mock.patch.dict(os.environ, {"SECRET_KEY": "b" * 64}):
            self.assertEqual(read_secret_key(), "b" * 64)

    def test_missing_or_short_key_is_refused_with_how_to_make_one(self) -> None:
        for key in (None, "", "   ", "a" * 31):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "SECRET_KEY.*token_hex"):
                    check_secret_key(key)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SECRET_KEY"):
                read_secret_key()

    def test_the_app_refuses_to_start_with_a_short_key(self) -> None:
        users = FakeUsers()
        services = Services(None, None, None, users, FakeAccess(users))
        with self.assertRaisesRegex(ValueError, "SECRET_KEY"):
            create_app(services, secret_key="too short")


if __name__ == "__main__":
    unittest.main()