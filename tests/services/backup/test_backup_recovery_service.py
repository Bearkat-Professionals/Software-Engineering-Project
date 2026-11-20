"""Unit tests for 2.5 Backup and Recovery (BackupRecoveryService). No database: the media,
the three 3.5 functions, the users, the scans, the engine and the audit trail are fakes.

    py -m unittest tests.services.backup.test_backup_recovery_service -v
"""
from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from backend.black_boxes.backup_media import BackupMediaError, StoredBackup
from backend.data_access.backup_recovery.backup_format import BackupKind
from backend.data_access.backup_recovery.restore_from_backup import RestoreError, RestoreResult
from backend.data_access.backup_recovery.verify_backup import VerificationResult
from backend.data_access.database_engine import LockNotAvailableError
from backend.data_access.network_data.scan_store import SCAN_OPEN_LOCK
from backend.records import Scan, User
from backend.records.enums import AlertEventName, AuditAction
from backend.services.backup import BackupAction, BackupRecoveryService, BackupStatus
from backend.services.backup.backup_recovery_service import CONFIRM_REASON, SCAN_RUNNING_REASON, backup_kind

LOG = "backend.services.backup.backup_recovery_service"
NOW = datetime(2026, 11, 17, 14, 5, 30, 123456)
NAME = "full-20261117-140000-000"
ADMIN, SAM = 1, 2


class FakeMedia:
    def __init__(self, names=(NAME,)) -> None:
        self.stored = [StoredBackup(name, 1000, "2026-11-17T14:00:00") for name in names]
        self.encryption_enabled = False

    def list(self) -> list[StoredBackup]:
        return list(self.stored)


class FakeEngine:
    """unit_of_work and named_lock, recording what happened in `events`."""

    def __init__(self, events: list[str], lock_free: bool = True) -> None:
        self.events = events
        self.lock_free = lock_free
        self.locks: list[tuple[str, float]] = []

    @contextmanager
    def unit_of_work(self):
        yield "unit"

    @contextmanager
    def named_lock(self, name: str, timeout_s: float):
        self.locks.append((name, timeout_s))
        if not self.lock_free:
            raise LockNotAvailableError(f"could not get the lock '{name}'")
        self.events.append("lock taken")
        try:
            yield "lock cursor"
        finally:
            self.events.append("lock released")


class FakeUsers:
    def __init__(self) -> None:
        self.users = {ADMIN: User(user_id=ADMIN, username="admin", password_hash="x"),
                      SAM: User(user_id=SAM, username="sam", password_hash="x")}

    def find(self, user_id: int, unit: Any = None) -> User | None:
        return self.users.get(user_id)


class FakeScans:
    def __init__(self, running: Scan | None = None) -> None:
        self.running = running
        self.asked_with: list[Any] = []

    def find_running(self, unit: Any = None) -> Scan | None:
        self.asked_with.append(unit)
        return self.running


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[tuple] = []

    def record_event(self, user_id, record_type, record_id, new_value, unit, action=AuditAction.UPDATE):
        self.entries.append((user_id, record_type, record_id, new_value, unit, action))


class Engine35:
    """The three 3.5 functions, as fakes that record each call in `events`."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.create_error: Exception | None = None
        self.safety_error: Exception | None = None
        self.problems: tuple[str, ...] = ()
        self.restore_error: Exception | None = None
        self.after_restore = None           # e.g. remove the admin from the "restored" accounts
        self.key_material_calls: list[tuple[str, dict]] = []
        self.hold = threading.Event()       # set: create returns at once
        self.hold.set()
        self.entered = threading.Event()

    def create(self, engine, media, name=None) -> StoredBackup:
        self.events.append(f"create {name or 'full'}")
        self.entered.set()
        self.hold.wait(5)
        error = self.safety_error if name else self.create_error
        if error is not None:
            raise error
        return StoredBackup(name or "full-20261117-140530-123", 2000, "2026-11-17T14:05:30")

    def verify(self, media, name, **key_material) -> VerificationResult:
        self.events.append(f"verify {name}")
        self.key_material_calls.append(("verify", key_material))
        return VerificationResult(name, self.problems)

    def restore(self, engine, media, name, **key_material) -> RestoreResult:
        self.events.append(f"restore {name}")
        self.key_material_calls.append(("restore", key_material))
        if self.restore_error is not None:
            raise self.restore_error
        if self.after_restore:
            self.after_restore()
        return RestoreResult(name, BackupKind.FULL, {"device": 3, "vendor": 2})


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.media = FakeMedia()
        self.engine = FakeEngine(self.events)
        self.users = FakeUsers()
        self.scans = FakeScans()
        self.audit = FakeAudit()
        self.fns = Engine35(self.events)
        self.service = self.make()

    def make(self) -> BackupRecoveryService:
        return BackupRecoveryService(self.engine, self.media, self.users, self.scans,
                                     create=self.fns.create, verify=self.fns.verify, restore=self.fns.restore,
                                     audit=self.audit, clock=lambda: NOW)

    def restore(self, name=NAME, confirm=NAME, user=ADMIN):
        return self.service.restore(name, confirm, user, on_start=lambda: self.events.append("on_start"))


# ---- 2.5.1 Request Backup -----------------------------------------------------------------

class CreateBackupTests(ServiceTestCase):
    def test_a_backup_is_taken_and_remembered(self) -> None:
        with self.assertLogs(LOG, "INFO") as logs:
            result = self.service.create_backup(ADMIN)
        self.assertEqual(result.status, BackupStatus.DONE)
        self.assertEqual(result.backup.name, "full-20261117-140530-123")
        self.assertEqual(self.events, ["create full"])
        self.assertIn("admin", logs.output[0])
        last = self.service.list_backups().last_attempt
        self.assertEqual((last.action, last.name, last.by, last.at, last.status, last.reason),
                         (BackupAction.BACKUP, "full-20261117-140530-123", "admin", NOW, BackupStatus.DONE, None))

    def test_a_failed_backup_is_failed_with_the_alert_and_no_path(self) -> None:
        disk = OSError(28, "No space left on device", r"C:\Users\Jason\backups\x.tmp")
        error = BackupMediaError(f"could not write backup 'x': {disk}")
        error.__cause__ = disk
        self.fns.create_error = error
        with self.assertLogs(LOG, "ERROR") as logs:
            result = self.service.create_backup(ADMIN)
        self.assertEqual(result.status, BackupStatus.FAILED)
        self.assertEqual(result.alerts, (AlertEventName.BACKUP_FAILED,))
        self.assertEqual(result.reasons, ("The backup failed: it couldn't be saved in the backup folder "
                                          "(No space left on device).",))
        self.assertNotIn("Users", result.reasons[0])                          # no file path for the screen
        self.assertTrue(any("Traceback" in line for line in logs.output))      # the details are in the log
        self.assertEqual(self.service.list_backups().last_attempt.status, BackupStatus.FAILED)

    def test_a_database_error_is_failed_too(self) -> None:
        self.fns.create_error = RuntimeError("Lost connection to MySQL server")
        with self.assertLogs(LOG, "ERROR"):
            result = self.service.create_backup(ADMIN)
        self.assertEqual(result.status, BackupStatus.FAILED)
        self.assertIn("RuntimeError", result.reasons[0])
        self.assertEqual(result.alerts, (AlertEventName.BACKUP_FAILED,))

    def test_a_second_call_while_one_runs_is_busy(self) -> None:
        self.fns.hold.clear()                                                 # the first backup stays open
        first: list = []
        with self.assertLogs(LOG, "INFO"):
            worker = threading.Thread(target=lambda: first.append(self.service.create_backup(SAM)))
            worker.start()
            self.assertTrue(self.fns.entered.wait(2))
            second = self.service.create_backup(ADMIN)
            busy_restore = self.restore()
            listing = self.service.list_backups()
            self.fns.hold.set()
            worker.join(5)

        self.assertEqual(second.status, BackupStatus.BUSY)
        self.assertEqual(second.reasons, ("A backup is already running (started by sam at 14:05).",))
        self.assertEqual(busy_restore.status, BackupStatus.BUSY)
        self.assertEqual((listing.busy.action, listing.busy.by, listing.busy.since), (BackupAction.BACKUP, "sam", NOW))
        self.assertEqual(self.events.count("create full"), 1)                 # only the first one ran
        self.assertNotIn(f"restore {NAME}", self.events)
        self.assertEqual(first[0].status, BackupStatus.DONE)
        self.assertIsNone(self.service.list_backups().busy)                   # free again afterwards

    def test_free_again_after_a_failure(self) -> None:
        self.fns.create_error = RuntimeError("boom")
        with self.assertLogs(LOG, "ERROR"):
            self.service.create_backup(ADMIN)
        self.fns.create_error = None
        with self.assertLogs(LOG, "INFO"):
            self.assertEqual(self.service.create_backup(ADMIN).status, BackupStatus.DONE)


# ---- 2.5.3 Report Backup Status -------------------------------------------------------------

class ListAndVerifyTests(ServiceTestCase):
    def test_the_list_has_each_backups_kind_newest_first(self) -> None:
        self.media = FakeMedia(["before-restore-20261117-150000-000", NAME, "partial-20261116-090000-000"])
        listing = self.make().list_backups()
        self.assertEqual([(b.name, b.kind) for b in listing.backups],
                         [("before-restore-20261117-150000-000", "safety"), (NAME, "full"),
                          ("partial-20261116-090000-000", "partial")])
        self.assertEqual((listing.backups[0].size_bytes, listing.backups[0].created), (1000, "2026-11-17T14:00:00"))
        self.assertFalse(listing.backups[0].encrypted)
        self.assertIn("unencrypted", listing.warning)
        self.assertIsNone(listing.last_attempt)
        self.assertIsNone(listing.busy)
        self.assertEqual(backup_kind("my-own-name"), "other")

    def test_verify_a_good_and_a_bad_backup(self) -> None:
        with self.assertLogs(LOG, "INFO"):
            self.assertEqual(self.service.verify(NAME).problems, ())
            self.fns.problems = ("table 'device' has 3 rows, expected 4",)
            result = self.service.verify(NAME)
        self.assertEqual((result.status, result.problems), (BackupStatus.DONE, self.fns.problems))

    def test_verify_passes_key_material_without_logging_it(self) -> None:
        seen = {}

        def verify(media, name, **key_material):
            seen.update(key_material)
            return VerificationResult(name, ())

        service = BackupRecoveryService(self.engine, self.media, self.users, self.scans,
                                        create=self.fns.create, verify=verify, restore=self.fns.restore,
                                        audit=self.audit, clock=lambda: NOW)
        with self.assertLogs(LOG, "INFO") as logs:
            service.verify(NAME, private_key_pem="PRIVATE", passphrase="top secret")
        self.assertEqual(seen, {"private_key_pem": "PRIVATE", "passphrase": "top secret"})
        self.assertNotIn("PRIVATE", " ".join(logs.output))
        self.assertNotIn("top secret", " ".join(logs.output))

    def test_verify_an_unknown_name(self) -> None:
        result = self.service.verify("nope")
        self.assertEqual((result.status, result.reasons), (BackupStatus.NOT_FOUND, ("There's no backup named 'nope'.",)))
        self.assertEqual(self.events, [])


# ---- 2.5.2 Request Restore --------------------------------------------------------------------

class RestoreTests(ServiceTestCase):
    def test_a_restore_in_order(self) -> None:
        with self.assertLogs(LOG, "WARNING") as logs:
            result = self.restore()
        self.assertEqual(result.status, BackupStatus.DONE, result.reasons)
        self.assertEqual(self.events, ["lock taken", f"verify {NAME}", "create before-restore-20261117-140530-123",
                                       "on_start", f"restore {NAME}", "lock released"])
        self.assertEqual(self.engine.locks, [(SCAN_OPEN_LOCK, 5)])
        self.assertEqual(self.scans.asked_with, ["lock cursor"])              # checked while holding the lock
        self.assertEqual(result.safety_backup, "before-restore-20261117-140530-123")
        self.assertEqual(result.rows_restored, {"device": 3, "vendor": 2})
        self.assertIn(f"restore of {NAME} by admin: done", logs.output[-1])
        self.assertTrue(logs.output[-1].startswith("WARNING"))
        self.assertEqual(self.audit.entries, [(ADMIN, "restore", 0, {
            "backup": NAME, "safety_backup": "before-restore-20261117-140530-123",
            "rows_restored": {"device": 3, "vendor": 2}}, "unit", AuditAction.UPDATE)])
        self.assertEqual(self.service.list_backups().last_attempt.action, BackupAction.RESTORE)

    def test_restore_uses_key_material_once_per_read_and_never_logs_it(self) -> None:
        with self.assertLogs(LOG, "WARNING") as logs:
            result = self.service.restore(NAME, NAME, ADMIN, private_key_pem="PRIVATE",
                                          passphrase="top secret",
                                          on_start=lambda: self.events.append("on_start"))
        self.assertTrue(result.ok)
        expected = {"private_key_pem": "PRIVATE", "passphrase": "top secret"}
        self.assertEqual(self.fns.key_material_calls, [("verify", expected), ("restore", expected)])
        rendered = " ".join(logs.output)
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn("top secret", rendered)

    def assertRefused(self, result, status, reason=None) -> None:
        self.assertEqual(result.status, status)
        if reason is not None:
            self.assertEqual(result.reasons, (reason,))
        self.assertNotIn(f"restore {NAME}", self.events)                     # nothing restored
        self.assertFalse(any(event.startswith("create") for event in self.events))   # nothing created
        self.assertNotIn("on_start", self.events)
        self.assertEqual(self.audit.entries, [])

    def test_the_wrong_confirmation_is_refused_first(self) -> None:
        for typed in ("", "full", NAME.upper(), NAME + " "):
            with self.subTest(typed=typed):
                self.assertRefused(self.restore(confirm=typed), BackupStatus.REJECTED, CONFIRM_REASON)
        self.assertRefused(self.restore(name="nope", confirm="other"), BackupStatus.REJECTED, CONFIRM_REASON)

    def test_an_unknown_backup_is_not_found(self) -> None:
        self.assertRefused(self.restore(name="nope", confirm="nope"), BackupStatus.NOT_FOUND,
                           "There's no backup named 'nope'.")
        self.assertEqual(self.engine.locks, [])

    def test_a_running_scan_is_busy(self) -> None:
        self.scans.running = Scan(scan_id=7, user_id=SAM, target_scope="192.168.1.0/24")
        with self.assertLogs(LOG, "INFO"):
            self.assertRefused(self.restore(), BackupStatus.BUSY, SCAN_RUNNING_REASON)
        self.assertEqual(self.events, ["lock taken", "lock released"])

    def test_a_scan_starting_holds_the_lock_busy(self) -> None:
        self.engine.lock_free = False
        with self.assertLogs(LOG, "INFO"):
            self.assertRefused(self.restore(), BackupStatus.BUSY, SCAN_RUNNING_REASON)

    def test_a_backup_that_fails_verification_changes_nothing(self) -> None:
        self.fns.problems = ("table 'device' checksum doesn't match",)
        with self.assertLogs(LOG, "INFO"):
            result = self.restore()
        self.assertRefused(result, BackupStatus.REJECTED)
        self.assertEqual(result.problems, self.fns.problems)

    def test_a_failed_safety_backup_means_no_on_start_and_no_restore(self) -> None:
        self.fns.safety_error = RuntimeError("Lost connection")
        with self.assertLogs(LOG, "INFO"):
            result = self.restore()
        self.assertEqual(result.status, BackupStatus.FAILED)
        self.assertIn("The safety backup couldn't be taken, so nothing was restored", result.reasons[0])
        self.assertEqual(result.alerts, (AlertEventName.BACKUP_FAILED,))
        self.assertNotIn("on_start", self.events)
        self.assertNotIn(f"restore {NAME}", self.events)

    def test_a_restore_error_is_failed_and_nothing_changed(self) -> None:
        self.fns.restore_error = RestoreError("table 'scan' no longer has column(s): x")
        with self.assertLogs(LOG, "INFO"):
            result = self.restore()
        self.assertEqual(result.status, BackupStatus.FAILED)
        self.assertEqual(result.reasons, ("Nothing was changed: table 'scan' no longer has column(s): x",))
        self.assertEqual(result.safety_backup, "before-restore-20261117-140530-123")    # it was still taken
        self.assertEqual(self.audit.entries, [])
        self.assertIn("lock released", self.events)

    def test_an_unexpected_error_is_failed_and_frees_the_service(self) -> None:
        self.fns.restore_error = RuntimeError("deadlock")
        with self.assertLogs(LOG, "INFO"):
            result = self.restore()
        self.assertEqual(result.status, BackupStatus.FAILED)
        self.assertTrue(result.reasons[0].startswith("Nothing was changed"))
        self.assertIsNone(self.service.list_backups().busy)

    def test_no_audit_entry_when_the_admin_isnt_in_the_restored_accounts(self) -> None:
        self.fns.after_restore = lambda: self.users.users.pop(ADMIN)
        with self.assertLogs(LOG, "WARNING") as logs:
            result = self.restore()
        self.assertEqual(result.status, BackupStatus.DONE)
        self.assertEqual(self.audit.entries, [])
        self.assertTrue(any("not audited" in line and "audit_log.user_id" in line for line in logs.output))

    def test_if_on_start_fails_nothing_is_restored(self) -> None:
        def broken() -> None:
            raise RuntimeError("couldn't end the sessions")
        with self.assertLogs(LOG, "INFO"):
            result = self.service.restore(NAME, NAME, ADMIN, on_start=broken)
        self.assertEqual(result.status, BackupStatus.FAILED)
        self.assertTrue(result.reasons[0].startswith("Nothing was changed"))
        self.assertNotIn(f"restore {NAME}", self.events)
        self.assertEqual(self.audit.entries, [])

    def test_a_restore_while_another_restore_runs_is_busy(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_start() -> None:
            started.set()
            release.wait(5)

        results: list = []
        with self.assertLogs(LOG, "INFO"):
            worker = threading.Thread(target=lambda: results.append(
                self.service.restore(NAME, NAME, SAM, on_start=slow_start)))
            worker.start()
            self.assertTrue(started.wait(2))
            second = self.restore()
            backup = self.service.create_backup(ADMIN)
            release.set()
            worker.join(5)
        self.assertEqual(second.reasons, ("A restore is already running (started by sam at 14:05).",))
        self.assertEqual(backup.status, BackupStatus.BUSY)
        self.assertEqual(results[0].status, BackupStatus.DONE)


if __name__ == "__main__":
    unittest.main()
