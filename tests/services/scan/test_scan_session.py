"""Unit tests for the Scan Session Controller: open (2.2.1.1, #45) and close (2.2.1.4, #46).
No database: FakeScanStore follows the real ScanStore's rules.

    python -m unittest discover tests
"""
from __future__ import annotations

import threading
import time
import unittest
from copy import deepcopy
from datetime import datetime

from backend.data_access.network_data import ScanNotRunningError
from backend.data_access.repository import RecordNotFoundError
from backend.records.enums import ScanOutcome, ScanStatus
from backend.records.scan import Scan
from backend.records.user import User
from backend.services.scan.scan_session import (MAX_ERROR_CHARS, MAX_ERROR_LINES, MAX_LINE_CHARS,
                                                STUCK_SCAN_MESSAGE, ScanSessionController, format_errors)
from backend.services.scan.scan_settings import ScanSettings

JAVIER = User(user_id=1, username="Javier", password_hash="not-a-real-hash")
JASON = User(user_id=2, username="Jason", password_hash="not-a-real-hash")
SCOPE = "192.168.1.0/24"
SETTINGS = ScanSettings()
STARTED = datetime(2026, 10, 30, 14, 3)


class FakeScanStore:
    """3.1's ScanStore in memory, with its rules: finish() refuses unknown and finished scans,
    and every method hands out copies, like rows read from a database."""
    def __init__(self, now: datetime = STARTED, start_delay: float = 0.0) -> None:
        self.scans: dict[int, Scan] = {}
        self.now = now
        self.start_delay = start_delay          # makes a race between two threads likely
        self.failing_finishes = 0               # how many finish() calls fail, like a lost connection

    def find(self, scan_id: int) -> Scan | None:
        return deepcopy(self.scans.get(scan_id))

    def find_running(self) -> Scan | None:
        running = [scan for scan in self.scans.values() if scan.status is ScanStatus.RUNNING]
        return deepcopy(running[-1]) if running else None

    def start(self, scan: Scan) -> Scan:
        time.sleep(self.start_delay)
        stored = deepcopy(scan)
        stored.scan_id = len(self.scans) + 1
        stored.status = ScanStatus.RUNNING
        stored.started_at = stored.started_at or self.now
        self.scans[stored.scan_id] = stored
        return deepcopy(stored)

    def finish(self, scan_id: int, outcome: ScanOutcome, error_message: str | None = None) -> Scan:
        if self.failing_finishes:
            self.failing_finishes -= 1
            raise ConnectionError("database went away")
        scan = self.scans.get(scan_id)
        if scan is None:
            raise RecordNotFoundError(f"scan {scan_id} does not exist")
        if scan.status is not ScanStatus.RUNNING:
            raise ScanNotRunningError(f"scan {scan_id} has already finished")
        scan.status = ScanStatus.COMPLETED
        scan.outcome = ScanOutcome(outcome)
        scan.error_message = error_message
        scan.ended_at = self.now
        return deepcopy(scan)

    def add_running(self, user_id: int) -> Scan:
        """A scan left 'running', as if the server stopped during it."""
        return self.start(Scan(user_id=user_id, target_scope=SCOPE))

    def latest(self) -> Scan | None:
        """Like ScanStore.latest: newest started_at first, then the highest scan_id."""
        newest = max(self.scans.values(), key=lambda scan: (scan.started_at, scan.scan_id), default=None)
        return deepcopy(newest)


class FakeUserStore:
    def __init__(self, users: tuple[User, ...] = (JAVIER, JASON)) -> None:
        self.users = {user.user_id: user for user in users}

    def find(self, user_id: int) -> User | None:
        return self.users.get(user_id)


class SessionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeScanStore()
        self.users = FakeUserStore()
        self.sessions = ScanSessionController(self.store, self.users)


class OpenScanTests(SessionTestCase):
    def test_open_creates_a_running_scan(self) -> None:
        result = self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS)
        self.assertTrue(result.opened)
        self.assertIsNone(result.reason)
        self.assertEqual((result.scan.user_id, result.scan.target_scope), (JAVIER.user_id, SCOPE))
        self.assertIs(result.scan.status, ScanStatus.RUNNING)
        self.assertEqual(result.scan.started_at, STARTED)
        self.assertEqual(result.scan.configuration_used, SETTINGS.to_configuration())

    def test_second_scan_is_refused_naming_who_started_the_first(self) -> None:
        self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS)
        second = self.sessions.open(SCOPE, JASON.user_id, SETTINGS)
        self.assertFalse(second.opened)
        self.assertEqual(second.reason, "A scan started by Javier at 14:03 is still running.")
        self.assertEqual(len(self.store.scans), 1)

    def test_two_controllers_share_one_answer(self) -> None:
        # The bug found in #45's first review: a second controller failed the running scan
        other = ScanSessionController(self.store, self.users)
        first = self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS)
        second = other.open(SCOPE, JASON.user_id, SETTINGS)
        self.assertFalse(second.opened)
        self.assertIs(self.store.scans[first.scan.scan_id].status, ScanStatus.RUNNING)

    def test_bad_scope_changes_nothing(self) -> None:
        stuck = self.store.add_running(JASON.user_id)
        result = self.sessions.open("8.8.8.0/24", JAVIER.user_id, SETTINGS)
        self.assertFalse(result.opened)
        self.assertIn("8.8.8.0/24", result.reason)
        self.assertEqual(len(self.store.scans), 1)
        self.assertIs(self.store.scans[stuck.scan_id].status, ScanStatus.RUNNING)

    def test_unknown_user_is_refused(self) -> None:
        result = self.sessions.open(SCOPE, 99, SETTINGS)
        self.assertFalse(result.opened)
        self.assertEqual(result.reason, "Your account was not found. Sign in again.")
        self.assertEqual(self.store.scans, {})

    def test_deleted_starter_is_called_another_user(self) -> None:
        self.store.add_running(user_id=99)
        result = self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS)
        self.assertEqual(result.reason, "A scan started by another user at 14:03 is still running.")

    def test_two_clicks_at_once_open_one_scan(self) -> None:
        self.store.start_delay = 0.05
        clicks = [threading.Thread(target=self.sessions.open, args=(SCOPE, user.user_id, SETTINGS))
                  for user in (JAVIER, JASON)]
        for click in clicks:
            click.start()
        for click in clicks:
            click.join()
        self.assertEqual(len(self.store.scans), 1)


class RecoverStuckScansTests(SessionTestCase):
    def test_every_stuck_scan_is_closed_as_failed(self) -> None:
        stuck = [self.store.add_running(JAVIER.user_id), self.store.add_running(JASON.user_id)]
        self.assertEqual(self.sessions.recover_stuck_scans(), 2)
        for scan in stuck:
            stored = self.store.scans[scan.scan_id]
            self.assertEqual((stored.outcome, stored.error_message), (ScanOutcome.FAILED, STUCK_SCAN_MESSAGE))
        self.assertTrue(self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS).opened)

    def test_nothing_running_closes_nothing(self) -> None:
        self.assertEqual(self.sessions.recover_stuck_scans(), 0)


class CloseScanTests(SessionTestCase):
    def open_scan(self) -> Scan:
        result = self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS)
        assert result.scan is not None
        return result.scan

    def test_close_records_outcome_end_time_and_errors(self) -> None:
        scan = self.open_scan()
        closed = self.sessions.close(scan.scan_id, ScanOutcome.PARTIAL, ["ping unavailable", "10.0.0.5: timeout"])
        self.assertIs(closed.status, ScanStatus.COMPLETED)
        self.assertEqual(closed.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(closed.ended_at, STARTED)
        self.assertEqual(closed.error_message, "ping unavailable\n10.0.0.5: timeout")
        self.assertTrue(self.sessions.open(SCOPE, JASON.user_id, SETTINGS).opened)

    def test_no_errors_stores_none(self) -> None:
        closed = self.sessions.close(self.open_scan().scan_id, ScanOutcome.SUCCESS)
        self.assertIsNone(closed.error_message)

    def test_outcome_may_be_its_text(self) -> None:
        closed = self.sessions.close(self.open_scan().scan_id, "failed", ["no network"])
        self.assertEqual(closed.outcome, ScanOutcome.FAILED)

    def test_closing_twice_changes_nothing(self) -> None:
        scan = self.open_scan()
        self.sessions.close(scan.scan_id, ScanOutcome.SUCCESS)
        self.store.now = datetime(2026, 10, 30, 15, 0)
        again = self.sessions.close(scan.scan_id, ScanOutcome.FAILED, ["late"])
        self.assertEqual((again.outcome, again.ended_at, again.error_message),
                         (ScanOutcome.SUCCESS, STARTED, None))

    def test_unknown_outcome_is_refused_before_saving(self) -> None:
        scan = self.open_scan()
        with self.assertRaises(ValueError):
            self.sessions.close(scan.scan_id, "sucess")
        self.assertIs(self.store.scans[scan.scan_id].status, ScanStatus.RUNNING)

    def test_unknown_scan_raises_and_is_not_retried(self) -> None:
        with self.assertRaises(RecordNotFoundError):
            self.sessions.close(42, ScanOutcome.SUCCESS)
        self.assertTrue(self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS).opened)   # no retry of scan 42

    def test_failed_save_is_raised_then_retried_by_the_next_open(self) -> None:
        scan = self.open_scan()
        self.store.failing_finishes = 1
        with self.assertRaisesRegex(ConnectionError, "database went away"):
            self.sessions.close(scan.scan_id, ScanOutcome.PARTIAL, ["ping unavailable"])
        self.assertIs(self.store.scans[scan.scan_id].status, ScanStatus.RUNNING)

        result = self.sessions.open(SCOPE, JASON.user_id, SETTINGS)
        self.assertTrue(result.opened)                      # not blocked by the scan that failed to close
        retried = self.store.scans[scan.scan_id]
        self.assertEqual((retried.outcome, retried.error_message), (ScanOutcome.PARTIAL, "ping unavailable"))

    def test_retry_that_still_fails_raises(self) -> None:
        scan = self.open_scan()
        self.store.failing_finishes = 2
        with self.assertRaises(ConnectionError):
            self.sessions.close(scan.scan_id, ScanOutcome.SUCCESS)
        with self.assertRaises(ConnectionError):
            self.sessions.open(SCOPE, JASON.user_id, SETTINGS)
        self.assertTrue(self.sessions.open(SCOPE, JASON.user_id, SETTINGS).opened)   # third time it works


class ErrorsCarriedToCloseTests(SessionTestCase):
    """The #47 review fixes: errors reported while running are saved, and the list is bounded."""

    def open_scan(self) -> Scan:
        result = self.sessions.open(SCOPE, JAVIER.user_id, SETTINGS)
        assert result.scan is not None
        return result.scan

    def test_errors_added_while_running_are_saved_at_close(self) -> None:
        scan = self.open_scan()
        self.sessions.add_error("ping unavailable: ping command not found")
        closed = self.sessions.close(scan.scan_id, ScanOutcome.PARTIAL)          # none passed in
        self.assertEqual(closed.error_message, "ping unavailable: ping command not found")

    def test_errors_passed_to_close_come_first(self) -> None:
        scan = self.open_scan()
        self.sessions.add_error("10.0.0.5: timeout")
        closed = self.sessions.close(scan.scan_id, ScanOutcome.FAILED, ["scan stopped: database went away"])
        self.assertEqual(closed.error_message, "scan stopped: database went away\n10.0.0.5: timeout")

    def test_only_the_first_errors_are_kept_while_running(self) -> None:
        self.open_scan()
        for n in range(MAX_ERROR_LINES + 30):
            self.sessions.add_error(f"host {n}: timeout")
        errors = self.sessions.status().errors
        self.assertEqual(len(errors), MAX_ERROR_LINES + 1)
        self.assertEqual(errors[0], "host 0: timeout")
        self.assertEqual(errors[-1], "... and 30 more")

    def test_close_keeps_the_reason_and_the_true_total(self) -> None:
        scan = self.open_scan()
        for n in range(MAX_ERROR_LINES + 30):
            self.sessions.add_error(f"host {n}: timeout")
        closed = self.sessions.close(scan.scan_id, ScanOutcome.FAILED, ["scan stopped: out of memory"])
        lines = closed.error_message.split("\n")
        # 50 kept + 30 counted while running; the reason takes the first place, pushing one more out
        self.assertEqual(lines[0], "scan stopped: out of memory")
        self.assertEqual(len(lines), MAX_ERROR_LINES + 1)
        self.assertEqual(lines[-1], "... and 31 more")

    def test_errors_belong_to_their_own_scan(self) -> None:
        first = self.open_scan()
        self.sessions.add_error("first scan's error")
        self.sessions.close(first.scan_id, ScanOutcome.PARTIAL)
        second = self.open_scan()
        closed = self.sessions.close(second.scan_id, ScanOutcome.SUCCESS)
        self.assertIsNone(closed.error_message)


class FormatErrorsTests(unittest.TestCase):
    def test_one_error_per_line(self) -> None:
        self.assertEqual(format_errors(["ping unavailable", "ARP cache empty"]), "ping unavailable\nARP cache empty")

    def test_nothing_to_report_is_none(self) -> None:
        for errors in ([], (), ["", "   "]):
            with self.subTest(errors=errors):
                self.assertIsNone(format_errors(errors))

    def test_line_breaks_inside_an_error_become_spaces(self) -> None:
        self.assertEqual(format_errors(["Traceback:\n  line 3\r\n  boom"]), "Traceback: line 3 boom")

    def test_a_single_string_is_kept_whole(self) -> None:
        self.assertEqual(format_errors("ping failed"), "ping failed")

    def test_exceptions_are_turned_into_text(self) -> None:
        self.assertEqual(format_errors([ConnectionError("database went away")]), "database went away")

    def test_too_many_lines_are_cut(self) -> None:
        text = format_errors([f"host {n} failed" for n in range(60)])
        lines = text.split("\n")
        self.assertEqual(len(lines), MAX_ERROR_LINES + 1)
        self.assertEqual(lines[-1], "... and 10 more")

    def test_one_huge_error_is_cut(self) -> None:
        text = format_errors(["x" * 5000])
        self.assertEqual(text, "x" * MAX_LINE_CHARS)

    def test_total_length_is_capped(self) -> None:
        # 19 lines of 500 characters plus newlines is 9,519; a 20th would pass 10,000
        text = format_errors(["y" * MAX_LINE_CHARS] * 30)
        lines = text.split("\n")
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[-1], "... and 11 more")
        self.assertLessEqual(len(text), MAX_ERROR_CHARS + len(lines[-1]))

    def test_hidden_errors_are_added_to_the_count(self) -> None:
        self.assertEqual(format_errors(["ping unavailable"], hidden=5), "ping unavailable\n... and 5 more")

    def test_only_hidden_errors_still_say_so(self) -> None:
        self.assertEqual(format_errors([], hidden=3), "... and 3 more")


if __name__ == "__main__":
    unittest.main()
                                         
                                         
                                         
                                



