# tests/shared_modules/test_save_device_record_db.py
"""Integration tests for SH-4 Save Device Record with the real 3.1 and 3.2 on the
TEST database (same one-time setup as test_repository.py). Skipped without it.

    .\\dev test -k save_device -v
"""
from __future__ import annotations

import unittest
from datetime import datetime

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


if __name__ == "__main__":
    unittest.main()
