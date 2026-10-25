"""Integration tests for backup and recovery (#18), against the TEST database.

Backups are written to a temporary folder, never to backups/. Skipped when
the test database is not available (see test_repository.py for the setup).
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.black_boxes.backup_media import BackupMedia, BackupMediaError
from backend.data_access.backup_recovery.backup_format import Backup, BackupKind, rows_checksum
from backend.data_access.backup_recovery.create_full_backup import create_full_backup
from backend.data_access.backup_recovery.create_partial_backup import (REFERENCE_TABLES,
                                                                       create_partial_backup)
from backend.data_access.backup_recovery.read_tables import read_snapshot
from backend.data_access.backup_recovery.restore_from_backup import RestoreError, restore_from_backup
from backend.data_access.backup_recovery.verify_backup import verify_backup
from backend.data_access.repository import Repository
from backend.records import AuditLog, Device, Scan, ScanObservation, User, Vendor
from backend.records.enums import AuditAction
from tests.data_access.test_repository import CLEANUP, connect_to_test_database


class BackupRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.media = BackupMedia(self.folder)
        self.vendor, self.device = self.add_sample_data()

    def repo(self, entity_type):
        return Repository(self.engine, entity_type)

    def add_sample_data(self):
        """Linked rows in several tables, including a microsecond timestamp."""
        user = self.repo(User).create(User(username="admin", password_hash="not-a-real-hash"))
        vendor = self.repo(Vendor).create(Vendor(vendor_name="Acme"))
        device = self.repo(Device).create(Device(mac_address="AA:BB:CC:00:00:01", vendor_id=vendor.vendor_id,
                                                 first_seen=datetime(2026, 10, 25, 9, 30)))
        scan = self.repo(Scan).create(Scan(user_id=user.user_id, target_scope="192.168.1.0/24",
                                           configuration_used={"timeout": 1}, started_at=datetime(2026, 10, 25, 9, 0)))
        self.repo(ScanObservation).create(ScanObservation(scan_id=scan.scan_id, device_id=device.device_id,
                                                          ip_address="192.168.1.5"))
        self.repo(AuditLog).create(AuditLog(user_id=user.user_id, record_type="device", record_id=device.device_id,
                                            action=AuditAction.INSERT, new_value={"mac": "AA:BB:CC:00:00:01"},
                                            changed_at=datetime(2026, 10, 25, 9, 30, 1, 123456)))
        return vendor, device

    def fingerprint(self):
        """Every table's checksum: equal fingerprints mean equal databases."""
        _, tables = read_snapshot(self.engine)
        return {table: rows_checksum(rows) for table, rows in tables.items()}

    # ---- the ticket's "Done when" -------------------------------------------------

    def test_restored_backup_matches_the_original(self):
        original = self.fingerprint()
        stored = create_full_backup(self.engine, self.media)

        self.repo(Device).delete(self.device.device_id)               # change the data
        self.repo(Vendor).create(Vendor(vendor_name="Added Later"))
        self.assertNotEqual(self.fingerprint(), original)

        result = restore_from_backup(self.engine, self.media, stored.name)
        self.assertEqual(result.kind, BackupKind.FULL)
        self.assertEqual(self.fingerprint(), original)

    # ---- 3.5.1, 3.5.2, 3.5.3 -------------------------------------------------------

    def test_full_backup_holds_every_table_and_verifies(self):
        stored = create_full_backup(self.engine, self.media)
        result = verify_backup(self.media, stored.name)
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(set(result.backup.tables), set(self.fingerprint()))   # all 13, incl. alert_setting_event
        self.assertNotIn("mac_unique_key", result.backup.tables["device"][0])  # generated column skipped

    def test_partial_backup_holds_only_the_reference_tables(self):
        stored = create_partial_backup(self.engine, self.media)
        backup = Backup.from_dict(self.media.load(stored.name))
        self.assertEqual(backup.manifest.kind, BackupKind.PARTIAL)
        self.assertEqual(set(backup.tables), set(REFERENCE_TABLES))

    # ---- 3.5.4: refused restores change nothing -------------------------------------

    def test_partial_restore_that_would_break_links_is_refused(self):
        stored = create_partial_backup(self.engine, self.media)
        newer = self.repo(Vendor).create(Vendor(vendor_name="Newer Co"))            # not in the backup
        self.repo(Device).create(Device(mac_address="AA:BB:CC:00:00:02", vendor_id=newer.vendor_id))
        before = self.fingerprint()

        with self.assertRaises(RestoreError):
            restore_from_backup(self.engine, self.media, stored.name)
        self.assertEqual(self.fingerprint(), before)

    def test_damaged_backup_is_refused_and_nothing_changes(self):
        stored = create_full_backup(self.engine, self.media)
        self.repo(Vendor).create(Vendor(vendor_name="After Backup"))
        before = self.fingerprint()

        path = self.folder / f"{stored.name}.json.gz"
        path.write_bytes(path.read_bytes()[:30])
        with self.assertRaises(BackupMediaError):
            restore_from_backup(self.engine, self.media, stored.name)
        self.assertEqual(self.fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
