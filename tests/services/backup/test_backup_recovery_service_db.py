"""AT-11 for 2.5 Backup and Recovery, against the TEST database: back up, change the data,
restore through the service, and every table matches the backup again.

Backups go to a temporary folder, never to backups/. Skipped without the test database
(see test_repository.py for the setup).

    py -m unittest tests.services.backup.test_backup_recovery_service_db -v
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.black_boxes.backup_media import BackupMedia
from backend.data_access.account_settings import UserStore
from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.backup_recovery.backup_format import rows_checksum
from backend.data_access.backup_recovery.read_tables import read_snapshot
from backend.data_access.network_data import ScanStore
from backend.data_access.repository import Repository
from backend.records import Device, Scan, User, Vendor
from backend.services.backup import BackupRecoveryService, BackupStatus
from tests.data_access.test_repository import CLEANUP, connect_to_test_database


class BackupRecoveryServiceDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.media = BackupMedia(Path(folder.name))
        self.admin = Repository(self.engine, User).create(User(username="admin", password_hash="not-a-real-hash"))
        self.vendor = Repository(self.engine, Vendor).create(Vendor(vendor_name="Acme"))
        self.device = Repository(self.engine, Device).create(Device(
            mac_address="AA:BB:CC:00:00:01", vendor_id=self.vendor.pk, first_seen=datetime(2026, 11, 17, 9, 30)))
        self.service = BackupRecoveryService(self.engine, self.media, UserStore(self.engine), ScanStore(self.engine))

    def snapshot(self):
        _, tables = read_snapshot(self.engine)
        return tables

    def test_at_11_restore_puts_every_table_back(self):
        taken = self.service.create_backup(self.admin.pk)
        self.assertEqual(taken.status, BackupStatus.DONE, taken.reasons)
        before = self.snapshot()

        Repository(self.engine, Device).delete(self.device.pk)          # delete a device and a vendor
        Repository(self.engine, Vendor).delete(self.vendor.pk)
        self.assertNotEqual(rows_checksum(self.snapshot()["device"]), rows_checksum(before["device"]))

        result = self.service.restore(taken.backup.name, taken.backup.name, self.admin.pk)
        self.assertEqual(result.status, BackupStatus.DONE, result.reasons)
        after = self.snapshot()

        for table in before:
            if table == "audit_log":
                continue
            with self.subTest(table=table):
                self.assertEqual(rows_checksum(after[table]), rows_checksum(before[table]))
        # audit_log: the backup's rows, plus the one entry for this restore
        self.assertEqual(rows_checksum(after["audit_log"][:len(before["audit_log"])]),
                         rows_checksum(before["audit_log"]))
        [entry] = AuditTrail(self.engine).history("restore", 0)
        self.assertEqual((entry.user_id, entry.new_value["backup"], entry.new_value["safety_backup"]),
                         (self.admin.pk, taken.backup.name, result.safety_backup))
        self.assertEqual(entry.new_value["rows_restored"]["device"], 1)

        names = [backup.name for backup in self.service.list_backups().backups]
        self.assertIn(result.safety_backup, names)                      # the safety backup was really stored
        self.assertTrue(result.safety_backup.startswith("before-restore-"))

    def test_a_running_scan_refuses_the_restore(self):
        taken = self.service.create_backup(self.admin.pk)
        ScanStore(self.engine).start(Scan(user_id=self.admin.pk, target_scope="192.168.1.0/24"))
        Repository(self.engine, Device).delete(self.device.pk)
        result = self.service.restore(taken.backup.name, taken.backup.name, self.admin.pk)
        self.assertEqual(result.status, BackupStatus.BUSY)
        self.assertIsNone(Repository(self.engine, Device).get(self.device.pk))     # nothing restored
        self.assertEqual(len(self.service.list_backups().backups), 1)              # no safety backup either


if __name__ == "__main__":
    unittest.main()