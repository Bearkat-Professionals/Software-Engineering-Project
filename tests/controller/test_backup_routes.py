"""Tests for the backup API (2.5, #171), through Flask's test client with a fake backup
service and the real sessions. No database, no files.

    py -3.13 -m unittest tests.controller.test_backup_routes -v
"""
from __future__ import annotations

import unittest
from collections.abc import Callable
from datetime import datetime

from flask import Flask
from flask.testing import FlaskClient

from backend.black_boxes.backup_media import StoredBackup
from backend.controller.app_controller import Services, create_app
from backend.records import User
from backend.records.enums import AlertEventName
from backend.services.backup import (BackupAction, BackupListing, BackupResult, BackupStatus, LastAttempt,
                                     ListedBackup, Running)
from backend.services.backup.backup_recovery_service import CONFIRM_REASON, SCAN_RUNNING_REASON
from backend.services.scan.scan_session import ScanState, ScanStatusReport
from tests.controller.test_api_routes import FakeDevices, FakeScanManager, FakeSessions
from tests.controller.test_device_routes import ADA, SAM, VIEWER
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers

ROUTES_LOG = "backend.controller.backup_routes"
AUTH_LOG = "backend.controller.auth"
NAME = "full-20261117-140300"
STORED = StoredBackup(name=NAME, size_bytes=1843200, modified_at="2026-11-17T14:03:00")
AT = datetime(2026, 11, 17, 14, 3, 0)
SAVE_FAILED = "The backup failed: it couldn't be saved in the backup folder."   # the service's wording: no path
ALL_ROUTES = (("get", "/api/backups"), ("post", "/api/backups"), ("post", f"/api/backups/{NAME}/verify"),
              ("post", f"/api/backups/{NAME}/restore"))


class FakeBackups:
    """BackupRecoveryService in memory. `answer` is what the next create/verify/restore returns.
    restore calls on_start only when its answer is DONE, as the real service does once the
    restore really starts. `calls` is every call."""

    def __init__(self) -> None:
        self.answer: BackupResult | None = None
        self.calls: list[tuple] = []
        self.listing = BackupListing(
            (ListedBackup(NAME, "full", "2026-11-17T14:03:00", 1843200),),
            LastAttempt(BackupAction.BACKUP, NAME, "ada", AT, BackupStatus.DONE), None)

    def list_backups(self) -> BackupListing:
        return self.listing

    def create_backup(self, user_id: int) -> BackupResult:
        self.calls.append(("create", user_id))
        return self.answer or BackupResult(BackupStatus.DONE, backup=STORED)

    def verify(self, name: str) -> BackupResult:
        self.calls.append(("verify", name))
        return self.answer or BackupResult(BackupStatus.DONE)

    def restore(self, name: str, confirm: str, user_id: int, *, on_start: Callable[[], None]) -> BackupResult:
        self.calls.append(("restore", name, confirm, user_id))
        result = self.answer or BackupResult(BackupStatus.DONE, safety_backup="before-restore-20261117-150000-123",
                                             rows_restored={"device": 41, "vendor": 38412})
        if result.ok:
            on_start()
        return result


class BackupTestCase(unittest.TestCase):

    def setUp(self) -> None:
        self.backups = FakeBackups()
        users = FakeUsers(SAM, VIEWER, ADA)
        self.services = Services(scan_manager=FakeScanManager(),
                                 scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                                 devices=FakeDevices(), users=users, access=FakeAccess(users), backups=self.backups)
        self.app: Flask = create_app(self.services, secret_key=SECRET_KEY)
        self.admin = self.sign_in(ADA)

    def sign_in(self, user: User) -> FlaskClient:
        client = self.app.test_client()
        response = client.post("/api/login", json={"username": user.username, "password": PASSWORD})
        assert response.status_code == 200, response.get_json()
        return client

    def call(self, client: FlaskClient, method: str, path: str, body: dict | None = None):
        return client.get(path) if method == "get" else client.post(path, json={} if body is None else body)

    def assertSignedIn(self, client: FlaskClient, signed_in: bool) -> None:
        self.assertEqual(client.get("/api/me").status_code, 200 if signed_in else 401)


# ---- the four routes -----------------------------------------------------------------------

class SuccessTests(BackupTestCase):

    def test_list(self) -> None:
        self.backups.listing = BackupListing(self.backups.listing.backups, self.backups.listing.last_attempt,
                                             Running(BackupAction.RESTORE, "sam", AT))
        response = self.admin.get("/api/backups")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "backups": [{"name": NAME, "kind": "full", "created_at": "2026-11-17T14:03:00", "size_bytes": 1843200}],
            "last_attempt": {"action": "backup", "name": NAME, "by": "ada", "at": "2026-11-17T14:03:00",
                             "status": "done", "reason": None},
            "busy": {"action": "restore", "by": "sam", "since": "2026-11-17T14:03:00"}})

    def test_nothing_yet(self) -> None:
        self.backups.listing = BackupListing((), None, None)
        self.assertEqual(self.admin.get("/api/backups").get_json(),
                         {"backups": [], "last_attempt": None, "busy": None})

    def test_back_up_now(self) -> None:
        response = self.admin.post("/api/backups", json={})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"backup": {"name": NAME, "kind": "full",
                                                          "created_at": "2026-11-17T14:03:00", "size_bytes": 1843200}})
        self.assertEqual(self.backups.calls, [("create", ADA.user_id)])

    def test_verify_good_and_bad(self) -> None:
        self.assertEqual(self.admin.post(f"/api/backups/{NAME}/verify", json={}).get_json(),
                         {"name": NAME, "ok": True, "problems": []})
        self.backups.answer = BackupResult(BackupStatus.DONE, problems=("Table vendor: 3 rows expected, 2 found.",))
        self.assertEqual(self.admin.post(f"/api/backups/{NAME}/verify", json={}).get_json(),
                         {"name": NAME, "ok": False, "problems": ["Table vendor: 3 rows expected, 2 found."]})

    def test_restore(self) -> None:
        with self.assertLogs(ROUTES_LOG, "WARNING"):                            # "signed out 0 other session(s)"
            response = self.admin.post(f"/api/backups/{NAME}/restore", json={"confirm": NAME})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"restored": NAME, "safety_backup": "before-restore-20261117-150000-123",
                                               "rows_restored": {"device": 41, "vendor": 38412}, "signed_out": True})
        self.assertEqual(self.backups.calls, [("restore", NAME, NAME, ADA.user_id)])


# ---- refusals ----------------------------------------------------------------------------

class RefusalTests(BackupTestCase):

    def test_viewers_and_editors_get_403_on_every_route(self) -> None:
        for user in (VIEWER, SAM):
            client = self.sign_in(user)
            for method, path in ALL_ROUTES:
                with self.subTest(user=user.username, path=path, method=method):
                    with self.assertLogs(AUTH_LOG, "WARNING"):                  # each 403 is logged
                        self.assertEqual(self.call(client, method, path).status_code, 403)
        self.assertEqual(self.backups.calls, [])

    def test_signed_out_is_401(self) -> None:
        for method, path in ALL_ROUTES:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.call(self.app.test_client(), method, path).status_code, 401)

    def test_a_bad_name_is_404_like_an_unknown_one(self) -> None:
        for path in ("/api/backups/a%20b/verify", "/api/backups/..x/verify", "/api/backups/.hidden/restore",
                     "/api/backups/..%2Fx/verify", "/api/backups/a;b/restore"):
            with self.subTest(path=path):
                self.assertEqual(self.admin.post(path, json={"confirm": "x"}).status_code, 404)
        self.assertEqual(self.backups.calls, [])                # the service was never asked

    def test_service_refusals_map_to_status_codes(self) -> None:
        cases = [(BackupStatus.REJECTED, CONFIRM_REASON, 400),
                 (BackupStatus.NOT_FOUND, "There's no backup named 'x'.", 404),
                 (BackupStatus.BUSY, SCAN_RUNNING_REASON, 409),
                 (BackupStatus.FAILED, "Nothing was changed: the backup couldn't be read.", 500)]
        for status, reason, code in cases:
            with self.subTest(status=status):
                self.backups.answer = BackupResult(status, (reason,))
                response = self.admin.post(f"/api/backups/{NAME}/restore", json={"confirm": NAME})
                self.assertEqual(response.status_code, code)
                self.assertEqual(response.get_json(), {"error": reason, "reasons": [reason]})

    def test_a_backup_that_fails_verification_lists_its_problems(self) -> None:
        self.backups.answer = BackupResult(BackupStatus.REJECTED,
                                           ("The backup didn't pass verification, so nothing was changed.",),
                                           problems=("Table vendor: 3 rows expected, 2 found.",))
        body = self.admin.post(f"/api/backups/{NAME}/restore", json={"confirm": NAME}).get_json()
        self.assertEqual(body["problems"], ["Table vendor: 3 rows expected, 2 found."])

    def test_busy_backup_is_409(self) -> None:
        self.backups.answer = BackupResult(BackupStatus.BUSY,
                                           ("A backup is already running (started by sam at 14:05).",))
        self.assertEqual(self.admin.post("/api/backups", json={}).status_code, 409)

    def test_a_failed_backup_is_500_and_logs_the_alert(self) -> None:
        self.backups.answer = BackupResult(BackupStatus.FAILED, (SAVE_FAILED,), alerts=(AlertEventName.BACKUP_FAILED,))
        with self.assertLogs(ROUTES_LOG, "INFO") as logs:
            response = self.admin.post("/api/backups", json={})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], SAVE_FAILED)
        self.assertIn("would send alert 'backup_failed'", logs.output[0])

    def test_a_missing_or_wrong_confirm_reaches_the_service_as_text(self) -> None:
        for body in ({}, {"confirm": 42}, {"confirm": "nope"}):
            with self.subTest(body=body):
                self.backups.calls.clear()
                self.backups.answer = BackupResult(BackupStatus.REJECTED, (CONFIRM_REASON,))
                self.assertEqual(self.admin.post(f"/api/backups/{NAME}/restore", json=body).status_code, 400)
                self.assertIsInstance(self.backups.calls[0][2], str)

    def test_non_json_is_415(self) -> None:
        self.assertEqual(self.admin.post("/api/backups", data="x", content_type="text/plain").status_code, 415)


# ---- signing everyone out ----------------------------------------------------------------

class SignOutTests(BackupTestCase):

    def test_a_restore_signs_everyone_out_and_ends_every_hold(self) -> None:
        editor, other_admin = self.sign_in(SAM), self.sign_in(ADA)
        self.services.edit_holds.take(("device", 7), SAM.user_id, "sam-session")
        with self.assertLogs(ROUTES_LOG, "WARNING") as logs:
            response = self.admin.post(f"/api/backups/{NAME}/restore", json={"confirm": NAME})
        self.assertEqual(response.status_code, 200)
        self.assertIn("signed out 2 other session(s)", logs.output[0])
        for client in (editor, other_admin, self.admin):        # the admin's own session too
            self.assertSignedIn(client, False)
        self.assertEqual(self.services.edit_holds.count(), 0)

    def test_the_reply_drops_the_cookie(self) -> None:
        with self.assertLogs(ROUTES_LOG, "WARNING"):
            response = self.admin.post(f"/api/backups/{NAME}/restore", json={"confirm": NAME})
        self.assertIn("session=;", response.headers.get("Set-Cookie", ""))

    def test_a_refused_restore_signs_nobody_out(self) -> None:
        editor = self.sign_in(SAM)
        self.services.edit_holds.take(("device", 7), SAM.user_id, "sam-session")
        self.backups.answer = BackupResult(BackupStatus.REJECTED, (CONFIRM_REASON,))
        self.assertEqual(self.admin.post(f"/api/backups/{NAME}/restore", json={"confirm": "wrong"}).status_code, 400)
        self.assertSignedIn(editor, True)
        self.assertSignedIn(self.admin, True)
        self.assertEqual(self.services.edit_holds.count(), 1)


class NoPathsTests(BackupTestCase):

    def test_no_reply_contains_a_file_path(self) -> None:
        replies = [self.admin.get("/api/backups"), self.admin.post("/api/backups", json={}),
                   self.admin.post(f"/api/backups/{NAME}/verify", json={})]
        self.backups.answer = BackupResult(BackupStatus.FAILED, (SAVE_FAILED,))
        replies.append(self.admin.post("/api/backups", json={}))
        for response in replies:
            text = response.get_data(as_text=True)
            for sign in ("\\\\", ".json.gz", "/backups/", "C:"):
                self.assertNotIn(sign, text)


if __name__ == "__main__":
    unittest.main()
