"""Integration test for 2.3.1 Device Record Maintenance with the real SH-4 and 3.1
on the TEST database (same one-time setup as test_repository.py). Skipped without it.

    .\\dev test -k device_maintenance -v
"""
from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime

from backend.data_access.account_settings import UserStore
from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.network_data import DeviceStore
from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import Repository
from backend.records import Device, DeviceType, User, Vendor
from backend.records.enums import AuditAction, IdentificationLevel
from backend.services.records.device_maintenance import (DeviceEdit, DeviceMaintenance,
                                                         DeviceMaintenanceStatus as MaintenanceStatus)
from backend.shared_modules.save_device_record import DeviceChanges, DeviceRecordSaver, SaveStatus
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

TEST_MAC = "02:DE:AD:00:00:01"      # locally administered: never a real vendor's device
TEST_TYPE = "VG test scan type"


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
        self.devices = DeviceStore(self.engine)
        self.vendors = ReferenceStore(self.engine, Vendor)
        self.types = ReferenceStore(self.engine, DeviceType)
        names = UserStore(self.engine)
        self.saver = DeviceRecordSaver(self.devices, self.vendors, self.types, names)
        self.service = DeviceMaintenance(self.engine, self.saver, self.devices, names)
        self.audit = AuditTrail(self.engine)

    def clear(self) -> None:
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
            cursor.execute("DELETE FROM device_type WHERE category_name = %s", (TEST_TYPE,))

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
        self.assertIn("changed by sam-editor at", late.reasons[0])
        self.assertEqual(DeviceStore(self.engine).find(device_id).model, "Sam's")     # Ada's change not stored

    def test_scan_only_change_does_not_conflict_with_an_open_edit(self) -> None:
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam, ip="192.168.1.5")
        form = self.service.load(added.device.pk)

        scanned = self.saver.save(
            DeviceChanges(mac_address=TEST_MAC, current_ip="192.168.1.9", seen_at=datetime.now()),
            self.ada, IdentificationLevel.UNIDENTIFIED)
        self.assertEqual(scanned.status, SaveStatus.UPDATED)

        edited = self.service.edit(added.device.pk, DeviceEdit(model="TB-1"), self.sam, form.loaded_at)
        self.assertDone(edited)
        stored = self.devices.find(added.device.pk)
        self.assertEqual((stored.current_ip, stored.model), ("192.168.1.9", "TB-1"))

    def test_scan_that_fills_the_unknown_type_is_a_conflict(self) -> None:
        device_type = self.types.store(DeviceType(category_name=TEST_TYPE), self.sam)
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        form = self.service.load(added.device.pk)

        scanned = self.saver.save(DeviceChanges(mac_address=TEST_MAC, device_type_id=device_type.pk),
                                  self.ada, IdentificationLevel.PARTIAL)
        self.assertEqual(scanned.status, SaveStatus.UPDATED)

        edited = self.service.edit(added.device.pk, DeviceEdit(model="TB-1"), self.sam, form.loaded_at)
        self.assertEqual(edited.status, MaintenanceStatus.CONFLICT)
        self.assertEqual(edited.changed_by, self.ada)
        self.assertIn("changed by ada-editor at", edited.reasons[0])

    def test_scan_and_user_save_keep_both_changes(self) -> None:
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam, ip="192.168.1.5")
        form = self.service.load(added.device.pk)
        scan_saved = threading.Event()
        failures: list[BaseException] = []

        def slow_scan() -> None:
            try:
                with self.engine.unit_of_work() as unit:
                    outcome = self.saver.save(
                        DeviceChanges(mac_address=TEST_MAC, current_ip="192.168.1.9",
                                      seen_at=datetime.now()),
                        self.ada, IdentificationLevel.UNIDENTIFIED, unit)
                    if outcome.status is not SaveStatus.UPDATED:
                        raise AssertionError(outcome)
                    scan_saved.set()
                    time.sleep(0.3)                    # keep the row lock until the edit is waiting
            except BaseException as exc:               # surface thread failures in the test
                failures.append(exc)
                scan_saved.set()

        worker = threading.Thread(target=slow_scan)
        worker.start()
        scan_saved.wait()
        edited = self.service.edit(added.device.pk, DeviceEdit(model="TB-1"), self.sam, form.loaded_at)
        worker.join()

        if failures:
            raise failures[0]
        self.assertDone(edited)
        stored = self.devices.find(added.device.pk)
        self.assertEqual((stored.current_ip, stored.model), ("192.168.1.9", "TB-1"))

    # ---- what only the real database can show -----------------------------------------

    def test_loaded_at_is_never_older_than_the_last_change(self) -> None:
        """loaded_at and the audit time come from the same clock, so a form opened after a
        change is never older than that change (else it would conflict with itself)."""
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        form = self.service.load(added.device.pk)
        latest = self.audit.latest(Device.TABLE, added.device.pk)
        self.assertGreaterEqual(form.loaded_at, latest.changed_at)
        self.assertLess((form.loaded_at - latest.changed_at).total_seconds(), 60)

    def test_a_stale_delete_names_who_changed_it(self) -> None:
        added = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        adas_form = self.service.load(added.device.pk)
        self.assertDone(self.service.edit(added.device.pk, DeviceEdit(model="New"), self.sam, adas_form.loaded_at))
        late = self.service.delete(added.device.pk, self.ada, adas_form.loaded_at)
        self.assertEqual(late.status, MaintenanceStatus.CONFLICT)
        self.assertIn("changed by sam-editor at", late.reasons[0])
        self.assertIsNotNone(DeviceStore(self.engine).find(added.device.pk))      # not deleted

    def test_adding_a_known_mac_names_the_existing_device(self) -> None:
        first = self.service.add(TEST_MAC, DeviceEdit(), self.sam)
        again = self.service.add(TEST_MAC.lower(), DeviceEdit(), self.ada)
        self.assertEqual(again.status, MaintenanceStatus.ALREADY_EXISTS)
        self.assertEqual(again.existing_device_id, first.device.pk)
        self.assertEqual(len(self.audit.history(Device.TABLE, first.device.pk)), 1)


if __name__ == "__main__":
    unittest.main()
