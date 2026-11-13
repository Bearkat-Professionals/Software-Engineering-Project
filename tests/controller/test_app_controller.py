"""Tests for the Application Controller and entry point (#50), through Flask's test
client with fake services. No database or network.

    python -m unittest tests.controller.test_app_controller -v
"""
import os
import unittest
from unittest import mock

from backend.controller.app_controller import UNEXPECTED_ERROR, Services, create_app, on_scan_finished
from backend.main import read_port
from backend.records import User
from backend.records.enums import AlertEventName, ScanOutcome
from backend.services.scan.record_scan_results import RecordSummary
from backend.services.scan.scan_manager import AlertCandidate, ScanResult
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers

CONTROLLER_LOG = "backend.controller.app_controller"
AUTH_LOG = "backend.controller.auth"
DEV_USER = User(user_id=1, username="dev", password_hash="secret-hash")


def make_app(users: FakeUsers | None = None):
    users = users or FakeUsers(DEV_USER)
    services = Services(scan_manager=None, scan_sessions=None, devices=None, users=users, access=FakeAccess(users))
    return create_app(services, secret_key=SECRET_KEY)


def signed_in_client(app, host: str = "localhost"):
    """Signed in as DEV_USER. The cookie belongs to `host`, so sign in on the host you'll call."""
    client = app.test_client()
    client.post("/api/login", json={"username": DEV_USER.username, "password": PASSWORD},
                headers={"Host": host})
    return client


class HealthTests(unittest.TestCase):
    def test_ok_with_a_working_database(self):
        response = signed_in_client(make_app()).get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "database": "ok"})

    def test_503_with_a_failing_database(self):
        users = FakeUsers(DEV_USER)
        client = signed_in_client(make_app(users))
        users.working = False
        with self.assertLogs(AUTH_LOG, level="ERROR"):
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()["error"])
        self.assertNotIn("went away", response.get_data(as_text=True))

    def test_signed_out_is_401(self):
        with self.assertLogs(AUTH_LOG, level="WARNING"):
            self.assertEqual(make_app().test_client().get("/api/health").status_code, 401)


class ErrorTests(unittest.TestCase):
    def test_unknown_url_is_json_404(self):
        response = make_app().test_client().get("/api/nope")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.get_json()["error"])

    def test_wrong_method_is_json_405(self):
        response = make_app().test_client().post("/api/health", json={})
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
                client = signed_in_client(make_app(), host)
                self.assertEqual(client.get("/api/health", headers={"Host": host}).status_code, 200)


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

    def test_missing_secret_key_stops_start_up(self):
        from backend import main
        with mock.patch.dict(os.environ, {"APP_PORT": "5000"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "SECRET_KEY"):
                main.main()


class ShutdownTests(unittest.TestCase):
    """main() closes a scan left running when the server stops (Ctrl+C)."""

    def _run_main_until_ctrl_c(self, sessions):
        app = mock.Mock()
        app.run.side_effect = KeyboardInterrupt
        patches = {name: mock.DEFAULT for name in (
            "read_port", "read_secret_key", "DatabaseConfig", "ScanSettings", "DatabaseEngine", "UserStore",
            "AccessControlService", "ScanStore", "DeviceStore", "build_scan_manager")}
        with mock.patch.multiple("backend.main", **patches), \
                mock.patch("backend.main.ScanSessionController", return_value=sessions), \
                mock.patch("backend.main.create_app", return_value=app), \
                mock.patch("backend.main.logging.basicConfig"):
            from backend import main
            with self.assertRaises(KeyboardInterrupt):
                main.main()

    def test_running_scans_are_closed_at_start_and_at_shutdown(self):
        sessions = mock.Mock()
        sessions.recover_stuck_scans.return_value = 0
        self._run_main_until_ctrl_c(sessions)
        self.assertEqual(sessions.recover_stuck_scans.call_count, 2)

    def test_a_database_error_at_shutdown_is_logged_not_raised(self):
        sessions = mock.Mock()
        sessions.recover_stuck_scans.side_effect = [0, RuntimeError("MySQL gone")]
        with self.assertLogs("backend.main", "ERROR") as logs:
            self._run_main_until_ctrl_c(sessions)       # still ends with Ctrl+C, not RuntimeError
        self.assertIn("Couldn't close running scans", logs.output[0])


if __name__ == "__main__":
    unittest.main()