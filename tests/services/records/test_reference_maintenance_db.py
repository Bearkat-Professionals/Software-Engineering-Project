"""Integration test for 2.3.2 Reference Maintenance with the real 3.2 stores on the
TEST database (same one-time setup as test_repository.py). Skipped without it.

    .\\dev test -k reference_maintenance -v

CLEANUP doesn't clear device types or signatures (other tests rely on the seeded
ones), so this test removes only the rows it made: names starting "RM test".
"""
from __future__ import annotations

import threading
import time
import unittest

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.reference_data import OuiPrefixStore, ReferenceStore
from backend.data_access.repository import Repository
from backend.records import DeviceSignature, DeviceType, OuiPrefix, User, Vendor
from backend.records.enums import AuditAction, RecordStatus
from backend.services.records.reference_maintenance import (MaintenanceStatus, ReferenceKind,
                                                            ReferenceMaintenance)
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

OUR_ROWS = (
    "DELETE FROM device_signature WHERE pattern_value LIKE 'RM test%'",
    "DELETE s FROM device_signature s JOIN device_type t ON s.device_type_id = t.device_type_id "
    "WHERE t.category_name LIKE 'RM test%'",
    "DELETE FROM device_type WHERE category_name LIKE 'RM test%'",
)
ADD = {
    ReferenceKind.VENDOR: {"vendor_name": "RM test vendor"},
    ReferenceKind.DEVICE_TYPE: {"category_name": "RM test type"},
    ReferenceKind.SIGNATURE: {"pattern_value": "RM test pattern", "evidence_type": "mdns"},  # + device_type_id
}
EDIT = {
    ReferenceKind.VENDOR: {"vendor_name": "RM test vendor (renamed)"},
    ReferenceKind.DEVICE_TYPE: {"category_name": "RM test type (renamed)"},
    ReferenceKind.SIGNATURE: {"pattern_value": "RM test pattern 2", "resulting_model": "Model X"},
}


class ReferenceMaintenanceDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = connect_to_test_database()

    def setUp(self) -> None:
        self.clear()
        self.addCleanup(self.clear)
        self.user = Repository(self.engine, User).create(
            User(username="ref-admin", password_hash="not-a-real-hash")).pk
        self.service = ReferenceMaintenance(
            {ReferenceKind.VENDOR: ReferenceStore(self.engine, Vendor),
             ReferenceKind.DEVICE_TYPE: ReferenceStore(self.engine, DeviceType),
             ReferenceKind.SIGNATURE: ReferenceStore(self.engine, DeviceSignature)},
            OuiPrefixStore(self.engine), self.engine.unit_of_work)
        self.audit = AuditTrail(self.engine)

    def clear(self) -> None:
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP + OUR_ROWS:     # CLEANUP first: it removes devices and evidence that point at them
                cursor.execute(sql)

    def assertDone(self, result) -> None:
        self.assertEqual(result.status, MaintenanceStatus.DONE, result.reasons)

    # ---- the ticket's "Done when" ----------------------------------------------------

    def test_add_edit_retire_and_reactivate_one_of_each_kind(self) -> None:
        added_type = self.service.add(ReferenceKind.DEVICE_TYPE, ADD[ReferenceKind.DEVICE_TYPE], self.user)
        self.assertDone(added_type)
        values = {
            ReferenceKind.VENDOR: ADD[ReferenceKind.VENDOR],
            ReferenceKind.SIGNATURE: {**ADD[ReferenceKind.SIGNATURE], "device_type_id": added_type.record.pk},
        }
        added = {ReferenceKind.DEVICE_TYPE: added_type}
        for kind, kind_values in values.items():
            added[kind] = self.service.add(kind, kind_values, self.user)
            self.assertDone(added[kind])

        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                record_id = added[kind].record.pk
                edited = self.service.edit(kind, record_id, EDIT[kind], self.user, added[kind].changed_at)
                self.assertDone(edited)
                retired = self.service.retire(kind, record_id, self.user, edited.changed_at)
                self.assertDone(retired)
                back = self.service.reactivate(kind, record_id, self.user, retired.changed_at)
                self.assertDone(back)

                stored = ReferenceStore(self.engine, kind.record_class).find(record_id)
                self.assertEqual(stored.status, RecordStatus.ACTIVE)
                name = kind.name_field
                self.assertEqual(getattr(stored, name), EDIT[kind][name])

                entries = self.audit.history(kind.record_class.TABLE, record_id)
                self.assertEqual([entry.action for entry in entries],
                                 [AuditAction.INSERT, AuditAction.UPDATE, AuditAction.UPDATE, AuditAction.UPDATE])
                self.assertEqual({entry.user_id for entry in entries}, {self.user})     # names the user
                self.assertEqual([entry.new_value["status"] for entry in entries[1:]],
                                 ["active", "retired", "active"])

    def test_add_and_move_a_prefix_with_one_audit_entry_each(self) -> None:
        first = self.service.add(ReferenceKind.VENDOR, {"vendor_name": "RM test first"}, self.user)
        second = self.service.add(ReferenceKind.VENDOR, {"vendor_name": "RM test second"}, self.user)
        added = self.service.add_prefix(first.record.pk, "02-aa-bb", self.user)
        self.assertDone(added)
        moved = self.service.move_prefix("02:AA:BB", second.record.pk, self.user, added.changed_at)
        self.assertDone(moved)

        stored = OuiPrefixStore(self.engine).find("02:AA:BB")
        self.assertEqual(stored.vendor_id, second.record.pk)
        entries = self.audit.history(OuiPrefix.TABLE, stored.pk)
        self.assertEqual([(entry.action, entry.user_id) for entry in entries],
                         [(AuditAction.INSERT, self.user), (AuditAction.UPDATE, self.user)])

    # ---- what only the real database can show -----------------------------------------

    def test_a_duplicate_name_differing_only_in_case_names_the_existing_record(self) -> None:
        first = self.service.add(ReferenceKind.VENDOR, {"vendor_name": "RM test Acme"}, self.user)
        again = self.service.add(ReferenceKind.VENDOR, {"vendor_name": "rm TEST acme"}, self.user)
        self.assertEqual(again.status, MaintenanceStatus.DUPLICATE)
        self.assertEqual(again.existing_id, first.record.pk)
        self.assertEqual(len(self.audit.by_user(self.user)), 1)          # only the first add

    def test_a_change_made_after_loading_is_a_conflict_and_changes_nothing(self) -> None:
        added = self.service.add(ReferenceKind.VENDOR, {"vendor_name": "RM test stale"}, self.user)
        opened_at = added.changed_at                    # two admins open the vendor now
        renamed = self.service.edit(ReferenceKind.VENDOR, added.record.pk,
                                    {"vendor_name": "RM test fresh"}, self.user, opened_at)
        self.assertDone(renamed)                        # the first one saves

        late = self.service.retire(ReferenceKind.VENDOR, added.record.pk, self.user, opened_at)
        self.assertEqual(late.status, MaintenanceStatus.CONFLICT)     # the second one is told
        self.assertEqual(late.changed_by, self.user)
        self.assertEqual(ReferenceStore(self.engine, Vendor).find(added.record.pk).status, RecordStatus.ACTIVE)

    def test_an_edit_cannot_undo_a_concurrent_retire(self) -> None:
        added = self.service.add(ReferenceKind.VENDOR, {"vendor_name": "RM test race"}, self.user)
        opened = self.service.load(ReferenceKind.VENDOR, added.record.pk)
        store = ReferenceStore(self.engine, Vendor)
        retire_saved = threading.Event()
        failures: list[BaseException] = []

        def slow_retire() -> None:
            try:
                with self.engine.unit_of_work() as unit:
                    store.retire(added.record.pk, self.user, unit)
                    retire_saved.set()
                    time.sleep(0.3)                    # the edit must wait for this row lock
            except BaseException as exc:
                failures.append(exc)
                retire_saved.set()

        worker = threading.Thread(target=slow_retire)
        worker.start()
        retire_saved.wait()
        edited = self.service.edit(ReferenceKind.VENDOR, added.record.pk,
                                   {"vendor_name": "RM test race renamed"}, self.user,
                                   opened.loaded_at)
        worker.join()

        if failures:
            raise failures[0]
        self.assertEqual(edited.status, MaintenanceStatus.CONFLICT)
        stored = store.find(added.record.pk)
        self.assertEqual((stored.vendor_name, stored.status), ("RM test race", RecordStatus.RETIRED))

if __name__ == "__main__":
    unittest.main()
