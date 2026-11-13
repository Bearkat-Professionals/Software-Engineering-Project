# tests/shared_modules/test_save_device_record_db.py
"""Integration tests for SH-4 Save Device Record with the real 3.1 and 3.2 on the
TEST database (same one-time setup as test_repository.py). Skipped without it.

    .\\dev test -k save_device -v
"""
from __future__ import annotations

import unittest
from datetime import datetime

from backend.data_access.account_settings import UserStore
from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.network_data import DeviceStore
from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import Repository
from backend.records import AuditLog, Device, DeviceType, User, Vendor
from backend.records.enums import AuditAction, IdentificationLevel
from backend.shared_modules.save_device_record import (DeviceChanges, DeviceRecordSaver, SaveOutcome,
                                                       SaveStatus)
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

FIRST = datetime(2026, 10, 29, 12, 0, 0)     # whole seconds: MySQL DATETIME drops microseconds
LATER = datetime(2026, 10, 29, 13, 0, 0)
ROUTER_MAC = "70:13:01:65:8E:F7"
LAPTOP_MAC = "AC:DE:48:00:11:22"


class SaveDeviceRecordDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = connect_to_test_database()

    def setUp(self) -> None:
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        self.user = Repository(self.engine, User).create(
            User(username="scanner", password_hash="not-a-real-hash")).pk
        self.saver = DeviceRecordSaver(DeviceStore(self.engine),
                                       ReferenceStore(self.engine, Vendor),
                                       ReferenceStore(self.engine, DeviceType))

    def save(self, ip: str, seen_at: datetime, unit: object = None, mac: str = ROUTER_MAC,
             **fields: object) -> SaveOutcome:
        changes = DeviceChanges(mac_address=mac, current_ip=ip, seen_at=seen_at, **fields)
        return self.saver.save(changes, acting_user_id=self.user,
                               level=IdentificationLevel.UNIDENTIFIED, unit=unit)

    def device_rows(self) -> list[Device]:
        return Repository(self.engine, Device).list()

    def audit_for(self, device_id: int) -> list[AuditLog]:
        return AuditTrail(self.engine).history(Device.TABLE, device_id)

    # ---- the ticket's "Done when" ----------------------------------------------------

    def test_same_mac_twice_is_one_row_and_two_audit_entries(self) -> None:
        first = self.save("192.168.1.5", FIRST)
        second = self.save("192.168.1.9", LATER)

        self.assertEqual((first.status, second.status), (SaveStatus.CREATED, SaveStatus.UPDATED))
        rows = self.device_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].current_ip, "192.168.1.9")
        self.assertEqual((rows[0].first_seen, rows[0].last_seen), (FIRST, LATER))

        audit = self.audit_for(rows[0].pk)
        self.assertEqual([entry.action for entry in audit], [AuditAction.INSERT, AuditAction.UPDATE])
        self.assertEqual({entry.user_id for entry in audit}, {self.user})
        self.assertEqual(audit[1].old_value["current_ip"], "192.168.1.5")
        self.assertEqual(audit[1].new_value["current_ip"], "192.168.1.9")

    # ---- what only the real database can show -----------------------------------------

    def test_a_failure_later_in_the_unit_of_work_stores_nothing(self) -> None:
        with self.assertRaises(RuntimeError):
            with self.engine.unit_of_work() as unit:
                outcome = self.save("192.168.1.5", FIRST, unit)
                self.assertEqual(outcome.status, SaveStatus.CREATED)      # saved inside the unit...
                raise RuntimeError("the rest of this host's save failed")
        self.assertEqual(self.device_rows(), [])                         # ...and rolled back with it
        self.assertEqual(AuditTrail(self.engine).by_user(self.user), [])

    def test_a_rejection_does_not_spoil_the_callers_unit_of_work(self) -> None:     # PR note 2
        with self.engine.unit_of_work() as unit:
            bad = self.save("192.168.1.5", FIRST, unit, vendor_id=999)           # no vendor 999
            good = self.save("192.168.1.20", FIRST, unit, mac=LAPTOP_MAC)
        self.assertEqual(bad.status, SaveStatus.REJECTED)
        self.assertIn("no vendor with id 999", bad.reasons[0])
        self.assertEqual(good.status, SaveStatus.CREATED)
        self.assertEqual([row.mac_address for row in self.device_rows()], [LAPTOP_MAC])  # committed

    def test_new_device_gets_the_database_defaults_and_its_flag_is_stored(self) -> None:
        outcome = self.save("192.168.1.5", FIRST)
        self.assertEqual((outcome.device.vendor_id, outcome.device.device_type_id), (1, 1))
        stored = Repository(self.engine, Device).get(outcome.device.pk)
        self.assertTrue(stored.flagged_status)

    # ---- #103: user edits ------------------------------------------------------------

    def test_migration_002_column_exists(self) -> None:
        column = self.engine.fetch_one("SHOW COLUMNS FROM device LIKE 'flag_set_by_user'")
        self.assertIsNotNone(column, "run database/migrations/002_flag_set_by_user.sql on the test database")
        self.assertEqual((column["Null"], str(column["Default"])), ("NO", "0"))

    def test_a_user_flagged_device_survives_a_scan_save(self) -> None:
        created = self.save("192.168.1.5", FIRST)
        flagged = self.saver.save(DeviceChanges(mac_address=ROUTER_MAC, flag_set_by_user=True),
                                  acting_user_id=self.user, level=IdentificationLevel.UNIDENTIFIED)
        self.assertEqual(flagged.status, SaveStatus.UPDATED)

        scanned = self.saver.save(DeviceChanges(mac_address=ROUTER_MAC, current_ip="192.168.1.9", seen_at=LATER),
                                  acting_user_id=self.user, level=IdentificationLevel.IDENTIFIED)
        stored = Repository(self.engine, Device).get(created.device.pk)
        self.assertTrue(scanned.flagged)
        self.assertEqual((stored.flag_set_by_user, stored.flagged_status, stored.current_ip),
                         (True, True, "192.168.1.9"))

    def test_stale_loaded_at_on_the_real_store_is_a_conflict_and_changes_nothing(self) -> None:
        saver = DeviceRecordSaver(DeviceStore(self.engine), ReferenceStore(self.engine, Vendor),
                                  ReferenceStore(self.engine, DeviceType), UserStore(self.engine))
        created = self.save("192.168.1.5", FIRST)                   # audited now, so long "after" loaded_at
        before = Repository(self.engine, Device).get(created.device.pk)
        audit_before = self.audit_for(created.device.pk)

        outcome = saver.save(DeviceChanges(mac_address=ROUTER_MAC, device_name="Edited"), acting_user_id=self.user,
                             level=IdentificationLevel.UNIDENTIFIED, loaded_at=datetime(2000, 1, 1))

        self.assertEqual(outcome.status, SaveStatus.CONFLICT)
        self.assertEqual(outcome.changed_by, self.user)
        self.assertEqual(outcome.changed_at, audit_before[-1].changed_at)
        self.assertIn("changed by scanner at", outcome.reasons[0])
        self.assertEqual(Repository(self.engine, Device).get(created.device.pk), before)
        self.assertEqual(self.audit_for(created.device.pk), audit_before)


if __name__ == "__main__":
    unittest.main()
