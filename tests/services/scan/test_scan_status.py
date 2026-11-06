import json
import threading
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

from backend.records.enums import ScanOutcome, ScanStatus
from backend.services.scan.scan_session import (
    ScanPhase,
    ScanSessionController,
    ScanStatusReport,
)


START = datetime(2026, 11, 5, 18, 0, 0)


#Fakes
class FakeScanStore:
    """In-memory stand-in for 3.1's ScanStore: no database."""

    def __init__(self):
        self.scans = []

    def find(self, scan_id):
        for scan in self.scans:
            if scan.scan_id == scan_id:
                return scan
        return None

    def find_running(self):
        running = [scan for scan in self.scans if scan.is_running]
        return running[-1] if running else None

    def start(self, scan):
        scan.scan_id = len(self.scans) + 1
        scan.status = ScanStatus.RUNNING
        if scan.started_at is None:
            scan.started_at = START
        self.scans.append(scan)
        return scan

    def finish(self, scan_id, outcome, error_message=None):
        scan = self.find(scan_id)
        scan.status = ScanStatus.COMPLETED
        scan.outcome = ScanOutcome(outcome)
        scan.error_message = error_message
        scan.ended_at = START + timedelta(seconds=40)
        return scan

    def latest(self):
        return self.scans[-1] if self.scans else None


class FakeUser:
    def __init__(self, user_id, username):
        self.user_id = user_id
        self.username = username


class FakeUserStore:
    def __init__(self, *users):
        self.users = {user.user_id: user for user in users}

    def find(self, user_id):
        return self.users.get(user_id)


class FakeSettings:
    def to_configuration(self):
        return {"timeout_s": 1}


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def make_controller(clock=None):
    users = FakeUserStore(FakeUser(1, "jthomas"))
    return ScanSessionController(FakeScanStore(), users, clock or FakeClock(START))


#No Scans
class TestNoScans(unittest.TestCase):
    def test_says_no_scans_yet(self):
        report = make_controller().status()
        self.assertEqual(report.state, "none")
        self.assertIsNone(report.scan_id)
        self.assertEqual(report.errors, ())


#While Running
class TestWhileRunning(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(START)
        self.sessions = make_controller(self.clock)
        self.scan = self.sessions.open("192.168.1.0/24", 1, FakeSettings()).scan

    def test_reports_user_scope_and_start(self):
        report = self.sessions.status()
        self.assertEqual(report.state, "running")
        self.assertEqual(report.scan_id, self.scan.scan_id)
        self.assertEqual(report.scope, "192.168.1.0/24")
        self.assertEqual(report.started_by, "jthomas")
        self.assertEqual(report.started_at, START)

    def test_before_any_progress(self):
        report = self.sessions.status()
        self.assertIsNone(report.phase)
        self.assertEqual((report.done, report.total), (0, 0))

    def test_reports_phase_and_done_of_total(self):
        self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 120, 254)
        report = self.sessions.status()
        self.assertEqual(report.phase, ScanPhase.DISCOVERING_HOSTS)
        self.assertEqual((report.done, report.total), (120, 254))

    def test_phase_can_be_given_as_text(self):
        self.sessions.report_progress("saving_results", 9, 14)
        self.assertEqual(self.sessions.status().phase, ScanPhase.SAVING_RESULTS)

    def test_reports_errors_so_far(self):
        self.sessions.add_error("ping unavailable: ping command not found")
        self.sessions.add_error("ARP cache empty")
        self.assertEqual(self.sessions.status().errors,
                         ("ping unavailable: ping command not found", "ARP cache empty"))

    def test_blank_error_is_ignored(self):
        self.sessions.add_error("   ")
        self.assertEqual(self.sessions.status().errors, ())

    def test_seconds_elapsed(self):
        self.clock.now = START + timedelta(seconds=75)
        self.assertEqual(self.sessions.status().seconds_elapsed, 75)

    def test_unknown_starting_user(self):
        self.sessions.user_store.users.clear()
        self.assertEqual(self.sessions.status().started_by, "unknown user")

    def test_rejects_done_greater_than_total(self):
        with self.assertRaises(ValueError):
            self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 255, 254)

    def test_rejects_negative_numbers(self):
        for done, total in [(-1, 10), (0, -1)]:
            with self.subTest(done=done, total=total):
                with self.assertRaises(ValueError):
                    self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, done, total)

    def test_rejects_unknown_phase(self):
        with self.assertRaises(ValueError):
            self.sessions.report_progress("napping", 1, 2)


#After Close
class TestAfterClose(unittest.TestCase):
    def setUp(self):
        self.sessions = make_controller()
        scan = self.sessions.open("192.168.1.0/24", 1, FakeSettings()).scan
        self.sessions.report_progress(ScanPhase.CLOSING, 1, 1)
        self.sessions.close(scan.scan_id, ScanOutcome.PARTIAL,
                            ["ping unavailable: ping command not found", "timeout on 192.168.1.9"])

    def test_reports_outcome_times_and_user(self):
        report = self.sessions.status()
        self.assertEqual(report.state, "finished")
        self.assertEqual(report.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(report.started_at, START)
        self.assertEqual(report.ended_at, START + timedelta(seconds=40))
        self.assertEqual(report.started_by, "jthomas")

    def test_reports_saved_errors(self):
        self.assertEqual(self.sessions.status().errors,
                         ("ping unavailable: ping command not found", "timeout on 192.168.1.9"))

    def test_no_progress_after_close(self):
        report = self.sessions.status()
        self.assertIsNone(report.phase)
        self.assertIsNone(report.done)

    def test_progress_after_close_is_ignored(self):
        self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 5, 10)
        self.sessions.add_error("late error")
        self.assertNotIn("late error", self.sessions.status().errors)

    def test_next_scan_starts_with_fresh_progress(self):
        self.sessions.open("192.168.1.0/24", 1, FakeSettings())
        report = self.sessions.status()
        self.assertEqual(report.state, "running")
        self.assertEqual(report.errors, ())
        self.assertIsNone(report.phase)


#Report Is A Copy
class TestReportIsACopy(unittest.TestCase):
    def setUp(self):
        self.sessions = make_controller()
        self.sessions.open("192.168.1.0/24", 1, FakeSettings())
        self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 10, 254)
        self.sessions.add_error("first error")

    def test_report_cannot_be_changed(self):
        report = self.sessions.status()
        with self.assertRaises(FrozenInstanceError):
            report.done = 999
        self.assertIsInstance(report.errors, tuple)

    def test_later_progress_does_not_change_an_old_report(self):
        report = self.sessions.status()
        self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 200, 254)
        self.sessions.add_error("second error")
        self.assertEqual(report.done, 10)
        self.assertEqual(report.errors, ("first error",))

    def test_changing_to_dict_does_not_change_the_controller(self):
        data = self.sessions.status().to_dict()
        data["errors"].append("added by caller")
        data["done"] = 999
        report = self.sessions.status()
        self.assertEqual(report.errors, ("first error",))
        self.assertEqual(report.done, 10)


#To Dict
class TestToDict(unittest.TestCase):
    def test_running_report_as_plain_values(self):
        sessions = make_controller(FakeClock(START + timedelta(seconds=5)))
        sessions.open("192.168.1.0/24", 1, FakeSettings())
        sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 3, 254)
        data = sessions.status().to_dict()
        self.assertEqual(data["state"], "running")
        self.assertEqual(data["started_at"], "2026-11-05T18:00:00")
        self.assertIsNone(data["ended_at"])
        self.assertEqual(data["phase"], "discovering_hosts")
        self.assertEqual(data["seconds_elapsed"], 5)
        json.dumps(data)                                  # only plain values

    def test_finished_report_as_plain_values(self):
        sessions = make_controller()
        scan = sessions.open("192.168.1.0/24", 1, FakeSettings()).scan
        sessions.close(scan.scan_id, ScanOutcome.SUCCESS)
        data = sessions.status().to_dict()
        self.assertEqual(data["outcome"], "success")
        self.assertEqual(data["ended_at"], "2026-11-05T18:00:40")
        self.assertEqual(data["errors"], [])
        json.dumps(data)

    def test_no_scans_as_plain_values(self):
        data = ScanStatusReport(state="none").to_dict()
        self.assertEqual(data["state"], "none")
        json.dumps(data)


#Threads
class TestThreads(unittest.TestCase):
    def test_many_readers_while_one_thread_writes(self):
        sessions = make_controller()
        sessions.open("192.168.1.0/24", 1, FakeSettings())
        problems = []
        writing = threading.Event()
        writing.set()

        def write_progress():
            for _ in range(20):
                for done in range(255):
                    sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, done, 254)
                for done in range(15):
                    sessions.report_progress(ScanPhase.SAVING_RESULTS, done, 14)
                sessions.add_error("one more error")
            writing.clear()

        def read_status():
            while writing.is_set():
                try:
                    report = sessions.status()
                    if report.done is not None and report.done > report.total:
                        problems.append(f"{report.done} of {report.total}")
                except Exception as error:
                    problems.append(repr(error))

        readers = [threading.Thread(target=read_status) for _ in range(8)]
        writer = threading.Thread(target=write_progress)
        for thread in readers + [writer]:
            thread.start()
        for thread in readers + [writer]:
            thread.join(timeout=30)

        self.assertEqual(problems, [])
        self.assertEqual(len(sessions.status().errors), 20)

    def test_status_does_not_wait_for_the_controller_lock(self):
        sessions = make_controller()
        sessions.open("192.168.1.0/24", 1, FakeSettings())
        answered = threading.Event()

        with sessions.lock:                               # as if open() or close() were busy
            thread = threading.Thread(target=lambda: (sessions.status(), answered.set()))
            thread.start()
            self.assertTrue(answered.wait(timeout=2), "status() waited for open()/close()")
        thread.join()


if __name__ == "__main__":
    unittest.main()