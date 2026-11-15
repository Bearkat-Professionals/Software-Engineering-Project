"""Integration test for 2.3.1 Device Record Maintenance with the real SH-4 and 3.1
on the TEST database (same one-time setup as test_repository.py). Skipped without it.

    .\\dev test -k device_maintenance -v
"""
from __future__ import annotations

import unittest

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.network_data import DeviceStore
from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import Repository
from backend.records import Device, DeviceType, User, Vendor
from backend.records.enums import AuditAction
from backend.services.records.device_maintenance import DeviceEdit, DeviceMaintenance, MaintenanceStatus
from backend.shared_modules.save_device_record import DeviceRecordSaver
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

TEST_MAC = "02:DE:AD:00:00:01"      # locally administered: never a real vendor's device


class DeviceMaintenanceDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = connect_to_test_database()

    def setUp(self) -> None:
        self.clear()
        self.addCleanup(self.clear)                     # removes the test device whatever happens
        users = Repository(self.engine, User)
        self.sam = users.create(User(username="sam-editor", password_hash="not-a-real-hash")).pk
        self.ada = users.create(User(username="ada-editor", password_hash="not-a-real-hash")).pk
        devices = DeviceStore(self.engine)
        saver = DeviceRecordSaver(devices, ReferenceStore(self.engine, Vendor), ReferenceStore(self.engine, DeviceType))
        self.service = DeviceMaintenance(self.engine, saver, devices)
        self.audit = AuditTrail(self.engine)

    def clear(self) -> None:
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)

    def assertDone(self, result) -> None:
        self.assertEqual(result.status, MaintenanceStatus.DONE, result.reasons)

    # ---- the ticket's "Done when" ----------------------------------------------------

    def test_add_edit_mark_and_delete_with_one_audit_entry_each(self) -> None:
        added = self.service.add(TEST_MAC, DeviceEdit(device_name="Test box"), self.sam, ip="192.168.1.250")
        self.assertDone(added)
        device_id = added.device.pk

        form = self.service.load(device_id)
        edited = self.service.edit(device_id, DeviceEdit(model="TB-1"), self.sam, form.loaded_at)
        self.assertDone(edited)
        marked = self.service.set_mark(device_id, "trusted", self.sam, edited.changed_at)
        self.assertDone(marked)
        self.assertTrue(marked.device.trusted_status)

        deleted = self.service.delete(device_id, self.sam, marked.changed_at)
        self.assertDone(deleted)
        self.assertEqual(deleted.removed, {"observations_removed": 0, "evidence_removed": 0})
        self.assertIsNone(DeviceStore(self.engine).find(device_id))

        entries = self.audit.history(Device.TABLE, device_id)
        self.assertEqual([entry.action for entry in entries],
                         [AuditAction.INSERT, AuditAction.UPDATE, AuditAction.UPDATE, AuditAction.DELETE])
        self.assertEqual({entry.user_id for entry in entries}, {self.sam})        # names the user

    def test_a_second_editor_with_an_older_loaded_at_gets_a_conflict(self) -> None:
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        device_id = added.device.pk
        sams_form = self.service.load(device_id)            # both open the edit form
        adas_form = self.service.load(device_id)

        self.assertDone(self.service.edit(device_id, DeviceEdit(model="Sam's"), self.sam, sams_form.loaded_at))
        late = self.service.edit(device_id, DeviceEdit(model="Ada's"), self.ada, adas_form.loaded_at)

        self.assertEqual(late.status, MaintenanceStatus.CONFLICT)
        self.assertEqual(late.changed_by, self.sam)
        self.assertEqual(DeviceStore(self.engine).find(device_id).model, "Sam's")     # Ada's change not stored

    # ---- what only the real database can show -----------------------------------------

    def test_the_database_clock_agrees_with_the_audit_clock(self) -> None:
        """loaded_at comes from the database (NOW(6)) and the audit time from Python. If the
        two clocks or time zones disagreed, a fresh form would conflict with itself, or a
        stale one wouldn't."""
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        form = self.service.load(added.device.pk)
        latest = self.audit.latest(Device.TABLE, added.device.pk)
        self.assertGreaterEqual(form.loaded_at, latest.changed_at)
        self.assertLess((form.loaded_at - latest.changed_at).total_seconds(), 60)

    def test_adding_a_known_mac_names_the_existing_device(self) -> None:
        first = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        again = self.service.add(TEST_MAC.lower(), DeviceEdit(), self.ada)
        self.assertEqual(again.status, MaintenanceStatus.ALREADY_EXISTS)
        self.assertEqual(again.existing_device_id, first.device.pk)
        self.assertEqual(len(self.audit.history(Device.TABLE, first.device.pk)), 1)


if __name__ == "__main__":
    unittest.main()