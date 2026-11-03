"""Integration tests for the Scan Session Controller (#45, #46) with the real 3.1 ScanStore
and 3.3 UserStore on the TEST database (same setup as test_repository.py). Skipped without it.

    python -m unittest discover tests
"""
from __future__ import annotations

import unittest

from backend.data_access.account_settings import UserStore
from backend.data_access.network_data import ScanStore
from backend.data_access.repository import Repository
from backend.records import Scan, User
from backend.records.enums import ScanOutcome, ScanStatus
from backend.services.scan.scan_session import STUCK_SCAN_MESSAGE, ScanSessionController, format_errors
from backend.services.scan.scan_settings import ScanSettings
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

SCOPE = "192.168.1.0/24"
SETTINGS = ScanSettings(probe_workers=64, probe_timeout_s=0.5, discovery_methods=("ping",))


class ScanSessionDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = connect_to_test_database()

    def setUp(self) -> None:
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        self.user_id = Repository(self.engine, User).create(
            User(username="scanner", password_hash="not-a-real-hash")).pk
        self.scans = ScanStore(self.engine)
        self.sessions = ScanSessionController(self.scans, UserStore(self.engine))

    def open_scan(self) -> Scan:
        result = self.sessions.open(SCOPE, self.user_id, SETTINGS)
        self.assertTrue(result.opened, result.reason)
        assert result.scan is not None
        return result.scan

    def test_closed_as_partial_then_a_new_scan_opens(self) -> None:
        scan = self.open_scan()
        self.sessions.close(scan.scan_id, ScanOutcome.PARTIAL, ["ping unavailable", "10.0.0.5: timeout"])

        stored = self.scans.find(scan.scan_id)
        self.assertIs(stored.status, ScanStatus.COMPLETED)
        self.assertEqual(stored.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(stored.error_message, "ping unavailable\n10.0.0.5: timeout")
        self.assertIsNotNone(stored.ended_at)
        self.assertGreaterEqual(stored.ended_at, stored.started_at)
        self.assertEqual(stored.configuration_used, SETTINGS.to_configuration())    # through JSON and back

        second = self.sessions.open(SCOPE, self.user_id, SETTINGS)
        self.assertTrue(second.opened)
        self.assertEqual(len(Repository(self.engine, Scan).list()), 2)

    def test_closing_twice_keeps_the_first_close(self) -> None:
        scan = self.open_scan()
        first = self.sessions.close(scan.scan_id, ScanOutcome.SUCCESS)
        again = self.sessions.close(scan.scan_id, ScanOutcome.FAILED, ["late"])
        self.assertEqual((again.outcome, again.ended_at, again.error_message),
                         (ScanOutcome.SUCCESS, first.ended_at, None))

    def test_the_longest_error_text_fits_in_the_column(self) -> None:
        scan = self.open_scan()
        errors = ["€" * 1000] * 200                 # 3 bytes a character: the worst case for TEXT
        self.sessions.close(scan.scan_id, ScanOutcome.FAILED, errors)
        self.assertEqual(self.scans.find(scan.scan_id).error_message, format_errors(errors))

    def test_scan_left_running_by_a_crash_is_recovered(self) -> None:
        stuck = self.scans.start(Scan(user_id=self.user_id, target_scope=SCOPE))   # no controller knows it
        refused = self.sessions.open(SCOPE, self.user_id, SETTINGS)
        self.assertFalse(refused.opened)                                          # still blocks scans

        after_restart = ScanSessionController(self.scans, UserStore(self.engine))
        self.assertEqual(after_restart.recover_stuck_scans(), 1)
        stored = self.scans.find(stuck.scan_id)
        self.assertEqual((stored.outcome, stored.error_message), (ScanOutcome.FAILED, STUCK_SCAN_MESSAGE))
        self.assertTrue(after_restart.open(SCOPE, self.user_id, SETTINGS).opened)


if __name__ == "__main__":
    unittest.main()