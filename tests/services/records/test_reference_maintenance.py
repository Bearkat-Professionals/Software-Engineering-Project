"""Tests for 2.3.2 Reference Maintenance with fake stores: no database.

    py -m unittest tests.services.records.test_reference_maintenance -v
"""
from __future__ import annotations

import unittest
from datetime import timedelta

from backend.data_access.database_engine import IntegrityConstraintError
from backend.records import DeviceSignature, DeviceType, OuiPrefix, Vendor
from backend.records.enums import EvidenceType, RecordStatus
from backend.services.records.reference_maintenance import (MaintenanceResult, MaintenanceStatus,
                                                            ReferenceKind, ReferenceMaintenance,
                                                            usage_sentence)
from tests.fakes.fake_reference_maintenance import (FakeClock, FakeMaintenanceStore, FakePrefixStore,
                                                    FakeUnitOfWork)

ADMIN = 7
OTHER_ADMIN = 8
DONE = MaintenanceStatus.DONE
REJECTED = MaintenanceStatus.REJECTED

VENDOR, DEVICE_TYPE, SIGNATURE = ReferenceKind.VENDOR, ReferenceKind.DEVICE_TYPE, ReferenceKind.SIGNATURE

# A new valid record of each kind, and a valid edit, as a screen would send them
NEW = {
    VENDOR: {"vendor_name": "Sonos"},
    DEVICE_TYPE: {"category_name": "Smart speaker"},
    SIGNATURE: {"pattern_value": "_sonos._tcp", "evidence_type": "mdns", "device_type_id": 2},
}
EDIT = {
    VENDOR: {"vendor_name": "Acme Networks"},
    DEVICE_TYPE: {"category_name": "Mobile phone"},
    SIGNATURE: {"pattern_value": "Roku Ultra"},
}
STORED_ID = {VENDOR: 4, DEVICE_TYPE: 2, SIGNATURE: 30}       # a stored, active record of each kind
NAME_FIELD = {VENDOR: "vendor_name", DEVICE_TYPE: "category_name", SIGNATURE: "pattern_value"}


class MaintenanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.work = FakeUnitOfWork()
        self.vendors = FakeMaintenanceStore(Vendor, [
            Vendor(vendor_id=4, vendor_name="Acme"),
            Vendor(vendor_id=5, vendor_name="Apple"),
            Vendor(vendor_id=6, vendor_name="OldCo", status=RecordStatus.RETIRED),
        ], self.clock)
        self.types = FakeMaintenanceStore(DeviceType, [
            DeviceType(device_type_id=2, category_name="Phone"),
            DeviceType(device_type_id=3, category_name="Pager", status=RecordStatus.RETIRED),
        ], self.clock)
        self.signatures = FakeMaintenanceStore(DeviceSignature, [
            DeviceSignature(signature_id=30, device_type_id=2, pattern_value="roku", evidence_type=EvidenceType.SSDP),
            DeviceSignature(signature_id=31, device_type_id=3, pattern_value="beeper",
                            evidence_type=EvidenceType.MDNS, status=RecordStatus.RETIRED),
        ], self.clock, device_types=self.types)
        self.prefixes = FakePrefixStore([OuiPrefix(oui_id=50, oui_prefix="28:6F:B9", vendor_id=4)], self.clock)
        self.stores = {VENDOR: self.vendors, DEVICE_TYPE: self.types, SIGNATURE: self.signatures}
        self.service = ReferenceMaintenance(self.stores, self.prefixes, self.work, clock=self.clock)

    def assertStatus(self, result: MaintenanceResult, status: MaintenanceStatus, *reason_parts: str) -> None:
        self.assertIsInstance(result, MaintenanceResult)
        self.assertEqual(result.status, status, result.reasons)
        if status is DONE:
            self.assertEqual(result.reasons, ())
        else:
            self.assertTrue(result.reasons, "a refusal needs a reason a person can read")
            self.assertIsNone(result.record)
        for part in reason_parts:
            self.assertTrue(any(part in reason for reason in result.reasons), (part, result.reasons))

    def assertNothingStored(self) -> None:
        for store in (*self.stores.values(), self.prefixes):
            self.assertEqual(store.changes, [])

    def assertOneUnitOfWork(self) -> None:
        """Every store call of the operation ran in the one unit the service opened."""
        self.assertEqual(self.work.opened, 1)
        units = {unit for store in (*self.stores.values(), self.prefixes) for unit in store.units}
        self.assertEqual(units, {"unit-1"})


# ---- each operation for each kind ---------------------------------------------------------

class EachOperationForEachKindTests(MaintenanceTestCase):
    def test_add(self) -> None:
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                self.setUp()
                result = self.service.add(kind, NEW[kind], ADMIN)
                self.assertStatus(result, DONE)
                self.assertIsInstance(result.record, kind.record_class)
                self.assertIsNotNone(result.record.pk)
                self.assertEqual(self.stores[kind].changes, [("insert", result.record.pk, ADMIN)])
                self.assertEqual((result.changed_by, result.changed_at), (ADMIN, self.clock()))
                self.assertOneUnitOfWork()
                self.assertEqual(self.work.committed, 1)

    def test_edit(self) -> None:
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                self.setUp()
                record_id = STORED_ID[kind]
                result = self.service.edit(kind, record_id, EDIT[kind], ADMIN, loaded_at=self.clock())
                self.assertStatus(result, DONE)
                field = NAME_FIELD[kind]
                self.assertEqual(getattr(result.record, field), EDIT[kind][field])
                self.assertEqual(getattr(self.stores[kind].rows[record_id], field), EDIT[kind][field])
                self.assertEqual(self.stores[kind].changes, [("update", record_id, ADMIN)])
                self.assertOneUnitOfWork()

    def test_retire_then_reactivate(self) -> None:
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                self.setUp()
                record_id = STORED_ID[kind]
                retired = self.service.retire(kind, record_id, ADMIN, loaded_at=self.clock())
                self.assertStatus(retired, DONE)
                self.assertEqual(retired.record.status, RecordStatus.RETIRED)
                self.assertOneUnitOfWork()

                back = self.service.reactivate(kind, record_id, ADMIN, loaded_at=self.clock())
                self.assertStatus(back, DONE)
                self.assertEqual(back.record.status, RecordStatus.ACTIVE)
                self.assertEqual(self.stores[kind].changes, [("update", record_id, ADMIN)] * 2)

    def test_the_kind_can_be_given_as_text(self) -> None:
        self.assertStatus(self.service.add("vendor", {"vendor_name": "Roku"}, ADMIN), DONE)

    def test_an_edit_that_changes_nothing_stores_nothing(self) -> None:
        result = self.service.edit(VENDOR, 4, {"vendor_name": "  Acme "}, ADMIN, loaded_at=None)
        self.assertStatus(result, DONE)
        self.assertNothingStored()

    def test_an_edit_reads_the_record_under_a_lock(self) -> None:       # review: no lost updates
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                self.setUp()
                self.service.edit(kind, STORED_ID[kind], EDIT[kind], ADMIN, loaded_at=None)
                self.assertEqual(self.stores[kind].locked[0], STORED_ID[kind])

    def test_the_type_and_vendor_being_checked_are_locked_too(self) -> None:
        self.service.add(SIGNATURE, NEW[SIGNATURE], ADMIN)
        self.assertEqual(self.types.locked, [2])
        self.service.add_prefix(5, "3C:07:54", ADMIN)
        self.assertEqual(self.vendors.locked, [5])

    def test_an_edit_keeps_the_values_it_wasnt_sent(self) -> None:
        result = self.service.edit(SIGNATURE, 30, {"resulting_model": "Ultra 2024"}, ADMIN, loaded_at=None)
        self.assertStatus(result, DONE)
        self.assertEqual((result.record.pattern_value, result.record.evidence_type, result.record.resulting_model),
                         ("roku", EvidenceType.SSDP, "Ultra 2024"))


# ---- checking values (SH-1) --------------------------------------------------------------

class NameAndPatternEdgeTests(MaintenanceTestCase):
    def check_lengths(self, kind: ReferenceKind, cases: list[tuple[int, bool]]) -> None:
        field = NAME_FIELD[kind]
        for length, accepted in cases:
            with self.subTest(kind=kind.value, length=length):
                self.setUp()
                values = {**NEW[kind], field: "n" * length}
                result = self.service.add(kind, values, ADMIN)
                if accepted:
                    self.assertStatus(result, DONE)
                else:
                    self.assertStatus(result, REJECTED, f"{length} characters long")
                    self.assertNothingStored()

    def test_vendor_name_1_to_255(self) -> None:
        self.check_lengths(VENDOR, [(1, True), (2, True), (3, True), (255, True), (256, False)])

    def test_device_type_name_1_to_100(self) -> None:
        self.check_lengths(DEVICE_TYPE, [(1, True), (100, True), (101, False)])

    def test_signature_pattern_3_to_255(self) -> None:
        self.check_lengths(SIGNATURE, [(2, False), (3, True), (255, True), (256, False)])

    def test_only_spaces_is_rejected(self) -> None:
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                result = self.service.add(kind, {**NEW[kind], NAME_FIELD[kind]: "    "}, ADMIN)
                self.assertStatus(result, REJECTED, "is empty")
        self.assertNothingStored()

    def test_names_are_stored_trimmed(self) -> None:
        result = self.service.add(VENDOR, {"vendor_name": "  Sonos  "}, ADMIN)
        self.assertEqual(result.record.vendor_name, "Sonos")

    def test_editing_a_name_to_blank_is_rejected(self) -> None:
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                result = self.service.edit(VENDOR, 4, {"vendor_name": blank}, ADMIN, loaded_at=None)
                self.assertStatus(result, REJECTED, "The name is empty.")
        self.assertNothingStored()


class SignatureRuleTests(MaintenanceTestCase):
    def test_an_unknown_evidence_type_lists_the_four_a_signature_can_use(self) -> None:
        result = self.service.add(SIGNATURE, {**NEW[SIGNATURE], "evidence_type": "bluetooth"}, ADMIN)
        self.assertStatus(result, REJECTED, "'bluetooth' is not an evidence type",
                          "Use one of: mdns, ssdp, netbios_llmnr, reverse_dns.")
        self.assertNothingStored()

    def test_oui_is_refused_for_a_signature(self) -> None:            # review: it would never match
        for call in (lambda: self.service.add(SIGNATURE, {**NEW[SIGNATURE], "evidence_type": "OUI"}, ADMIN),
                     lambda: self.service.edit(SIGNATURE, 30, {"evidence_type": "oui"}, ADMIN, None)):
            self.assertStatus(call(), REJECTED,
                              "Signatures match mdns, ssdp, netbios_llmnr or reverse_dns evidence. "
                              "OUI prefixes are added to a vendor instead.")
        self.assertNothingStored()

    def test_evidence_type_ignores_case_and_spaces(self) -> None:
        result = self.service.add(SIGNATURE, {**NEW[SIGNATURE], "evidence_type": " MDNS "}, ADMIN)
        self.assertStatus(result, DONE)
        self.assertEqual(result.record.evidence_type, EvidenceType.MDNS)

    def test_a_retired_device_type_is_refused(self) -> None:
        result = self.service.add(SIGNATURE, {**NEW[SIGNATURE], "device_type_id": 3}, ADMIN)
        self.assertStatus(result, REJECTED, "'Pager' is retired")
        self.assertNothingStored()

    def test_moving_a_signature_to_a_retired_type_is_refused(self) -> None:
        result = self.service.edit(SIGNATURE, 30, {"device_type_id": 3}, ADMIN, loaded_at=None)
        self.assertStatus(result, REJECTED, "'Pager' is retired")
        self.assertNothingStored()

    def test_a_device_type_that_doesnt_exist_is_refused(self) -> None:
        result = self.service.add(SIGNATURE, {**NEW[SIGNATURE], "device_type_id": 99}, ADMIN)
        self.assertStatus(result, REJECTED, "no device type with id 99")
        self.assertNothingStored()

    def test_the_resulting_name_and_model_are_at_most_255(self) -> None:
        for field in ("resulting_device_name", "resulting_model"):
            for length, accepted in ((255, True), (256, False)):
                with self.subTest(field=field, length=length):
                    self.setUp()
                    result = self.service.add(SIGNATURE, {**NEW[SIGNATURE], field: "r" * length}, ADMIN)
                    if accepted:
                        self.assertStatus(result, DONE)
                    else:
                        self.assertStatus(result, REJECTED, f"The {field.replace('_', ' ')} is 256 characters")

    def test_a_blank_resulting_name_is_stored_as_none(self) -> None:
        result = self.service.add(SIGNATURE, {**NEW[SIGNATURE], "resulting_device_name": "   "}, ADMIN)
        self.assertStatus(result, DONE)
        self.assertIsNone(result.record.resulting_device_name)

    def test_a_signature_of_a_retired_type_cant_be_reactivated(self) -> None:
        result = self.service.reactivate(SIGNATURE, 31, ADMIN, loaded_at=None)
        self.assertStatus(result, REJECTED, "Reactivate the device type first")
        self.assertNothingStored()
        self.assertEqual(self.work.rolled_back, 1)


class EveryProblemAtOnceTests(MaintenanceTestCase):
    def test_every_problem_is_reported_and_nothing_is_stored(self) -> None:   # OUT-04
        values = {"pattern_value": "ab", "evidence_type": "bluetooth", "device_type_id": "two",
                  "resulting_model": "m" * 300, "status": "retired"}
        result = self.service.add(SIGNATURE, values, ADMIN)
        self.assertStatus(result, REJECTED, "'status' can't be set", "2 characters long",
                          "not an evidence type", "device type id must be a whole number",
                          "resulting model is 300 characters")
        self.assertEqual(len(result.reasons), 5)
        self.assertNothingStored()
        self.assertEqual(self.work.opened, 0)           # refused before the database was touched

    def test_missing_values_are_named(self) -> None:
        result = self.service.add(SIGNATURE, {}, ADMIN)
        self.assertStatus(result, REJECTED, "needs a pattern", "needs an evidence type", "needs a device type")

    def test_the_id_and_status_cant_be_set_through_values(self) -> None:
        for name in ("vendor_id", "status"):
            with self.subTest(name=name):
                result = self.service.edit(VENDOR, 4, {name: 9}, ADMIN, loaded_at=None)
                self.assertStatus(result, REJECTED, f"'{name}' can't be set")
        self.assertNothingStored()

    def test_values_that_arent_an_object(self) -> None:
        for values in (None, "Acme", ["Acme"]):
            with self.subTest(values=values):
                self.assertStatus(self.service.add(VENDOR, values, ADMIN), REJECTED, "JSON object")

    def test_an_empty_edit(self) -> None:
        self.assertStatus(self.service.edit(VENDOR, 4, {}, ADMIN, loaded_at=None), REJECTED, "Nothing to change")


# ---- each failure gets its own status --------------------------------------------------------

class FailureStatusTests(MaintenanceTestCase):
    def test_duplicate_names_ignoring_case(self) -> None:
        cases = [(VENDOR, {"vendor_name": "ACME"}, 4), (DEVICE_TYPE, {"category_name": "phone"}, 2),
                 (SIGNATURE, {"pattern_value": "ROKU", "evidence_type": "ssdp", "device_type_id": 2}, 30)]
        for kind, values, existing in cases:
            with self.subTest(kind=kind.value):
                result = self.service.add(kind, values, ADMIN)
                self.assertStatus(result, MaintenanceStatus.DUPLICATE, "already exists")
                self.assertEqual(result.existing_id, existing)
        self.assertNothingStored()

    def test_two_admins_adding_the_same_name_at_once(self) -> None:    # review: no 500
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                self.setUp()
                self.stores[kind].unique_index_refuses_next = True
                result = self.service.add(kind, NEW[kind], ADMIN)
                self.assertStatus(result, MaintenanceStatus.DUPLICATE, "already exists")
                self.assertIsNone(result.existing_id)
                self.assertEqual(self.work.rolled_back, 1)

    def test_other_integrity_errors_are_not_called_duplicates(self) -> None:
        def broken(*args, **kwargs):
            raise IntegrityConstraintError("Cannot add or update a child row: a foreign key constraint fails")
        self.vendors.store = broken
        with self.assertRaises(IntegrityConstraintError):
            self.service.add(VENDOR, NEW[VENDOR], ADMIN)

    def test_renaming_onto_another_name_is_a_duplicate(self) -> None:
        result = self.service.edit(VENDOR, 4, {"vendor_name": "apple"}, ADMIN, loaded_at=None)
        self.assertStatus(result, MaintenanceStatus.DUPLICATE, "A vendor with this name already exists.")
        self.assertEqual(result.existing_id, 5)
        self.assertEqual(self.work.rolled_back, 1)

    def test_the_unknown_vendor_and_type_are_protected(self) -> None:
        for kind in (VENDOR, DEVICE_TYPE):
            for name, call in (("edit", lambda k: self.service.edit(k, 1, EDIT[k], ADMIN, None)),
                               ("retire", lambda k: self.service.retire(k, 1, ADMIN, None))):
                with self.subTest(kind=kind.value, operation=name):
                    result = call(kind)
                    self.assertStatus(result, MaintenanceStatus.PROTECTED, f"'Unknown' {kind.label}")
        self.assertNothingStored()

    def test_not_found(self) -> None:
        for kind in ReferenceKind:
            for name, call in (("edit", lambda k: self.service.edit(k, 999, EDIT[k], ADMIN, None)),
                               ("retire", lambda k: self.service.retire(k, 999, ADMIN, None)),
                               ("reactivate", lambda k: self.service.reactivate(k, 999, ADMIN, None))):
                with self.subTest(kind=kind.value, operation=name):
                    result = call(kind)
                    self.assertStatus(result, MaintenanceStatus.NOT_FOUND, f"no {kind.label} with id 999")

    def test_changed_by_someone_else_since_loading_is_a_conflict(self) -> None:
        opened_at = self.clock()
        later = opened_at + timedelta(minutes=5)
        for kind in ReferenceKind:
            for name, call in (("edit", lambda k: self.service.edit(k, STORED_ID[k], EDIT[k], ADMIN, opened_at)),
                               ("retire", lambda k: self.service.retire(k, STORED_ID[k], ADMIN, opened_at))):
                with self.subTest(kind=kind.value, operation=name):
                    self.setUp()
                    self.stores[kind].changed_elsewhere(STORED_ID[kind], OTHER_ADMIN, later)
                    result = call(kind)
                    self.assertStatus(result, MaintenanceStatus.CONFLICT, "Someone else changed this",
                                      later.strftime("%H:%M"), "Reload")
                    self.assertEqual((result.changed_by, result.changed_at), (OTHER_ADMIN, later))
                    self.assertNothingStored()

    def test_no_conflict_with_your_own_last_change(self) -> None:
        first = self.service.edit(VENDOR, 4, {"vendor_name": "Acme Inc"}, ADMIN, loaded_at=self.clock())
        second = self.service.edit(VENDOR, 4, {"vendor_name": "Acme Corp"}, ADMIN, loaded_at=first.changed_at)
        self.assertStatus(second, DONE)


# ---- usage --------------------------------------------------------------------------

class UsageTests(MaintenanceTestCase):
    def test_the_sentence(self) -> None:
        cases = [
            (DEVICE_TYPE, {"device": 12, "device_signature": 3}, "12 devices and 3 signatures use this device type."),
            (DEVICE_TYPE, {"device": 1, "device_signature": 0}, "1 device uses this device type."),
            (DEVICE_TYPE, {"device": 0, "device_signature": 1}, "1 signature uses this device type."),
            (VENDOR, {"device": 2}, "2 devices use this vendor."),
            (VENDOR, {"device": 0}, "Nothing uses this vendor."),
            (SIGNATURE, {"identification_evidence": 5}, "5 device identifications use this signature."),
            (DEVICE_TYPE, {"device": 1, "device_signature": 1}, "1 device and 1 signature use this device type."),
        ]
        for kind, counts, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(usage_sentence(kind, counts), expected)

    def test_usage_of_a_stored_record(self) -> None:
        self.types.usage_counts[2] = {"device": 12, "device_signature": 3}
        usage = self.service.usage(DEVICE_TYPE, 2)
        self.assertEqual(usage.status, DONE)
        self.assertEqual(dict(usage.counts), {"device": 12, "device_signature": 3})
        self.assertEqual(usage.sentence, "12 devices and 3 signatures use this device type.")

    def test_usage_of_a_missing_record(self) -> None:
        usage = self.service.usage(VENDOR, 999)
        self.assertEqual(usage.status, MaintenanceStatus.NOT_FOUND)
        self.assertIn("no vendor with id 999", usage.sentence)


# ---- OUI prefixes ---------------------------------------------------------------------

class PrefixTests(MaintenanceTestCase):
    def test_add_stores_it_in_the_usual_form(self) -> None:
        result = self.service.add_prefix(5, "3c-07-54", ADMIN)
        self.assertStatus(result, DONE)
        self.assertEqual((result.record.oui_prefix, result.record.vendor_id), ("3C:07:54", 5))
        self.assertEqual(self.prefixes.changes, [("insert", result.record.pk, ADMIN)])
        self.assertOneUnitOfWork()

    def test_move_to_another_vendor(self) -> None:
        result = self.service.move_prefix("286fb9", 5, ADMIN, loaded_at=self.clock())
        self.assertStatus(result, DONE)
        self.assertEqual(result.record.vendor_id, 5)
        self.assertEqual(self.prefixes.changes, [("update", 50, ADMIN)])
        self.assertOneUnitOfWork()

    def test_a_prefix_that_already_belongs_to_a_vendor(self) -> None:
        result = self.service.add_prefix(5, "28:6f:b9", ADMIN)
        self.assertStatus(result, MaintenanceStatus.DUPLICATE, "28:6F:B9 already belongs to Acme.",
                          "Move it instead")
        self.assertFalse(any("vendor 4" in reason for reason in result.reasons))     # a name, not an id
        self.assertEqual(result.existing_id, 50)
        self.assertNothingStored()

    def test_two_admins_adding_the_same_prefix_at_once(self) -> None:
        self.prefixes.unique_index_refuses_next = True
        result = self.service.add_prefix(5, "3C:07:54", ADMIN)
        self.assertStatus(result, MaintenanceStatus.DUPLICATE, "3C:07:54 was just added by someone else")

    def test_the_vendor_must_be_active(self) -> None:
        for call in (lambda: self.service.add_prefix(6, "3C:07:54", ADMIN),
                     lambda: self.service.move_prefix("28:6F:B9", 6, ADMIN, None)):
            self.assertStatus(call(), REJECTED, "'OldCo' is retired")
        self.assertNothingStored()

    def test_the_vendor_must_exist(self) -> None:
        self.assertStatus(self.service.add_prefix(99, "3C:07:54", ADMIN), MaintenanceStatus.NOT_FOUND,
                          "no vendor with id 99")

    def test_the_unknown_vendor_cant_own_prefixes(self) -> None:
        for call in (lambda: self.service.add_prefix(1, "3C:07:54", ADMIN),
                     lambda: self.service.move_prefix("28:6F:B9", 1, ADMIN, None)):
            self.assertStatus(call(), MaintenanceStatus.PROTECTED, "'Unknown' vendor")
        self.assertNothingStored()

    def test_moving_a_prefix_that_isnt_stored(self) -> None:
        result = self.service.move_prefix("AA:BB:CC", 5, ADMIN, None)
        self.assertStatus(result, MaintenanceStatus.NOT_FOUND, "AA:BB:CC isn't stored", "Add it instead")

    def test_moving_a_prefix_someone_else_just_moved(self) -> None:
        later = self.clock() + timedelta(minutes=2)
        self.prefixes.changed_elsewhere(50, OTHER_ADMIN, later)
        result = self.service.move_prefix("28:6F:B9", 5, ADMIN, loaded_at=self.clock())
        self.assertStatus(result, MaintenanceStatus.CONFLICT, "Someone else changed this OUI prefix")
        self.assertNothingStored()

    def test_a_bad_prefix_and_a_bad_vendor_id_are_both_reported(self) -> None:
        result = self.service.add_prefix("five", "xyz", ADMIN)
        self.assertStatus(result, REJECTED, "'xyz' is not an OUI prefix", "vendor id must be a whole number")
        self.assertEqual(len(result.reasons), 2)
        self.assertEqual(self.work.opened, 0)

    def test_bad_prefixes(self) -> None:
        for text in ("", "   ", "28:6F", "28:6F:B9:00", "GG:00:00", None, 286):
            with self.subTest(text=text):
                self.assertStatus(self.service.add_prefix(5, text, ADMIN), REJECTED, "is not an OUI prefix")
        self.assertNothingStored()


class SetupTests(unittest.TestCase):
    def test_every_kind_needs_a_store(self) -> None:
        with self.assertRaisesRegex(ValueError, "signature"):
            ReferenceMaintenance({VENDOR: None, DEVICE_TYPE: None}, None, FakeUnitOfWork())


if __name__ == "__main__":
    unittest.main()