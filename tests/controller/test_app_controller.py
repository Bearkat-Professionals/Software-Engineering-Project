"""Tests for the Application Controller and entry point (#50), through Flask's test
client with fake services. No database or network.

    python -m unittest tests.controller.test_app_controller -v
"""
import os
import unittest
from unittest import mock

from flask import g, jsonify

from backend.controller.app_controller import UNEXPECTED_ERROR, Services, create_app, on_scan_finished
from backend.main import find_dev_user, read_port
from backend.records import User
from backend.records.enums import AlertEventName, ScanOutcome
from backend.services.scan.record_scan_results import RecordSummary
from backend.services.scan.scan_manager import AlertCandidate, ScanResult

CONTROLLER_LOG = "backend.controller.app_controller"
DEV_USER = User(user_id=1, username="dev", password_hash="secret-hash")


class FakeUsers:
    def __init__(self, working: bool = True) -> None:
        self.working = working

    def find(self, user_id):
        if not self.working:
            raise RuntimeError("database went away")
        return DEV_USER

    def find_by_username(self, username):
        return DEV_USER if username == DEV_USER.username else None


def make_app(users: FakeUsers | None = None):
    services = Services(scan_manager=None, scan_sessions=None, devices=None, users=users or FakeUsers())
    return create_app(services, DEV_USER)


class HealthTests(unittest.TestCase):
    def test_ok_with_a_working_database(self):
        response = make_app().test_client().get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "database": "ok"})

    def test_503_with_a_failing_database(self):
        client = make_app(FakeUsers(working=False)).test_client()
        with self.assertLogs(CONTROLLER_LOG, level="ERROR"):
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["database"], "unavailable")
        self.assertNotIn("went away", response.get_data(as_text=True))


class ErrorTests(unittest.TestCase):
    def test_unknown_url_is_json_404(self):
        response = make_app().test_client().get("/api/nope")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.get_json()["error"])

    def test_wrong_method_is_json_405(self):
        response = make_app().test_client().post("/api/health")
        self.assertEqual(response.status_code, 405)
        self.assertTrue(response.get_json()["error"])

    def test_unexpected_error_is_a_generic_500_and_the_details_are_logged(self):
        app = make_app()

        @app.get("/boom")
        def boom():
            raise RuntimeError("the secret detail")

        with self.assertLogs(CONTROLLER_LOG, level="ERROR") as logs:
            response = app.test_client().get("/boom")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": UNEXPECTED_ERROR})
        self.assertNotIn("the secret detail", response.get_data(as_text=True))
        self.assertIn("the secret detail", "\n".join(logs.output))
        self.assertNotIn(DEV_USER.password_hash, "\n".join(logs.output))

    def test_another_host_header_is_refused(self):
        response = make_app().test_client().get("/api/health", headers={"Host": "evil.example.com"})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.get_json()["error"])
        for host in ("127.0.0.1:5000", "localhost:5000"):
            with self.subTest(host=host):
                self.assertEqual(make_app().test_client().get("/api/health", headers={"Host": host}).status_code, 200)


class DevelopmentUserTests(unittest.TestCase):
    def test_every_request_reaches_its_route_with_the_development_user(self):
        app = make_app()

        @app.get("/who")
        def who():
            return jsonify(username=g.user.username)

        client = app.test_client()
        for _ in range(2):
            self.assertEqual(client.get("/who").get_json(), {"username": "dev"})


class OnScanFinishedTests(unittest.TestCase):
    def test_logs_the_outcome_and_the_alert_count_not_each_device(self):
        alerts = (AlertCandidate(AlertEventName.NEW_UNIDENTIFIED_DEVICE, 7),
                  AlertCandidate(AlertEventName.NEW_UNIDENTIFIED_DEVICE, 8))
        result = ScanResult(5, ScanOutcome.SUCCESS, RecordSummary(created=2, updated=1), (), alerts, closed=True)
        with self.assertLogs(CONTROLLER_LOG, level="INFO") as logs:
            on_scan_finished(result)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("Scan 5 finished: success (2 created, 1 updated", logs.output[0])
        self.assertIn("'new_unidentified_device': 2 event(s)", logs.output[1])

    def test_failed_scan_with_no_summary(self):
        result = ScanResult(6, ScanOutcome.FAILED, None, ("stopped",), (AlertCandidate(AlertEventName.SCAN_FAILED),),
                            closed=True)
        with self.assertLogs(CONTROLLER_LOG, level="INFO") as logs:
            on_scan_finished(result)
        self.assertIn("Scan 6 finished: failed (nothing saved, 1 error(s))", logs.output[0])
        self.assertIn("'scan_failed': 1 event(s)", logs.output[1])


class StartUpTests(unittest.TestCase):
    def test_port_defaults_and_accepts_a_valid_number(self):
        for text, port in ((None, 5000), ("", 5000), ("8080", 8080), (" 65535 ", 65535)):
            env = {} if text is None else {"APP_PORT": text}
            with self.subTest(text=text), mock.patch.dict(os.environ, env):
                if text is None:
                    os.environ.pop("APP_PORT", None)
                self.assertEqual(read_port(), port)

    def test_bad_port_is_refused_with_a_clear_message(self):
        for text in ("abc", "0", "65536", "-1", "50.5"):
            with self.subTest(text=text), mock.patch.dict(os.environ, {"APP_PORT": text}):
                with self.assertRaisesRegex(ValueError, "APP_PORT"):
                    read_port()

    def test_dev_user_found(self):
        with mock.patch.dict(os.environ, {"DEV_USERNAME": "dev"}):
            self.assertIs(find_dev_user(FakeUsers()), DEV_USER)

    def test_dev_user_missing_or_not_set_stops_start_up(self):
        for name, expected in (("nobody", "no user with that name"), ("  ", "not set")):
            with self.subTest(name=name), mock.patch.dict(os.environ, {"DEV_USERNAME": name}):
                with self.assertRaisesRegex(SystemExit, expected):
                    find_dev_user(FakeUsers())


if __name__ == "__main__":
    unittest.main()
