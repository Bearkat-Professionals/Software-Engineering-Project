"""Tests for 3.2 Reference Data (#16).

normalize_prefix needs no database. Everything else runs against the TEST
database (same one-time setup as test_repository.py) and is skipped without it.

    python -m unittest discover tests
"""
import unittest

from backend.data_access.reference_data import (DuplicateReferenceError, InvalidPrefixError,
                                                OuiPrefixStore, ProtectedReferenceError,
                                                ReferenceStore, find_reference_rules,
                                                normalize_prefix)
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import AuditLog, Device, DeviceSignature, DeviceType, User, Vendor
from backend.records.enums import EvidenceType, RecordStatus
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

# CLEANUP keeps device types and signatures; these tests add their own, so clear them too
EXTRA_CLEANUP = ("DELETE FROM device_signature", "DELETE FROM device_type WHERE device_type_id <> 1")


class NormalizePrefixTests(unittest.TestCase):
    def test_common_spellings_become_one_form(self):
        for text in ("28:6F:B9", "28-6f-b9", "286fb9", "28.6F.B9", " 28 6f b9 "):
            with self.subTest(text=text):
                self.assertEqual(normalize_prefix(text), "28:6F:B9")

    def test_anything_but_six_hex_digits_is_rejected(self):
        for text in ("", None, "28:6F", "28:6F:B9:00", "ZZ:11:22", "286FB"):
            with self.subTest(text=text):
                with self.assertRaises(InvalidPrefixError):
                    normalize_prefix(text)


class ReferenceDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP + EXTRA_CLEANUP:
                cursor.execute(sql)
        self.admin = Repository(self.engine, User).create(
            User(username="admin", password_hash="not-a-real-hash")).user_id
        self.vendors = ReferenceStore(self.engine, Vendor)
        self.types = ReferenceStore(self.engine, DeviceType)
        self.signatures = ReferenceStore(self.engine, DeviceSignature)
        self.prefixes = OuiPrefixStore(self.engine)

    def audit_entries(self, table):
        return Repository(self.engine, AuditLog).list(record_type=table)

    # ---- the ticket's "Done when" -------------------------------------------------

    def test_a_duplicate_oui_prefix_is_rejected(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        other = self.vendors.store(Vendor(vendor_name="Other Co"), self.admin)
        self.prefixes.add("28:6F:B9", acme.pk, self.admin)
        with self.assertRaises(DuplicateReferenceError):
            self.prefixes.add("28-6f-b9", other.pk, self.admin)          # same prefix, other spelling
        self.assertEqual(self.prefixes.find("28:6F:B9").vendor_id, acme.pk)

    def test_a_vendor_with_two_prefixes_loads_correctly(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        self.prefixes.add("3C:07:54", acme.pk, self.admin)
        self.prefixes.add("28:6F:B9", acme.pk, self.admin)
        rules = find_reference_rules(self.engine)
        self.assertEqual(rules.prefixes_by_vendor[acme.pk], ("28:6F:B9", "3C:07:54"))
        self.assertEqual(rules.vendor_for_prefix("3c:07:54").vendor_name, "Acme")

    def test_retiring_a_vendor_used_by_devices_leaves_them_intact(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        devices = Repository(self.engine, Device)
        device = devices.create(Device(mac_address="28:6F:B9:00:00:01", vendor_id=acme.pk))
        self.assertEqual(self.vendors.usage(acme.pk), {"device": 1})

        self.vendors.retire(acme.pk, self.admin)
        self.assertEqual(self.vendors.find(acme.pk).status, RecordStatus.RETIRED)
        self.assertEqual(devices.get(device.pk).vendor_id, acme.pk)      # untouched
        self.assertTrue(self.vendors.in_use(acme.pk))

    # ---- 3.2.2 Store ---------------------------------------------------------------

    def test_duplicate_names_are_rejected_whatever_the_case(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        with self.assertRaises(DuplicateReferenceError):
            self.vendors.store(Vendor(vendor_name="ACME"), self.admin)
        self.vendors.store(acme, self.admin)                              # saving without renaming is fine
        with self.assertRaises(DuplicateReferenceError):
            self.types.store(DeviceType(category_name="UNKNOWN"), self.admin)

    def test_duplicate_signature_is_rejected_but_another_evidence_type_is_not(self):
        speaker = self.types.store(DeviceType(category_name="smart speaker"), self.admin)
        signature = dict(device_type_id=speaker.pk, pattern_value="_amzn-wplay._tcp")
        self.signatures.store(DeviceSignature(evidence_type=EvidenceType.MDNS, **signature), self.admin)
        with self.assertRaises(DuplicateReferenceError):
            self.signatures.store(DeviceSignature(evidence_type=EvidenceType.MDNS, **signature), self.admin)
        self.signatures.store(DeviceSignature(evidence_type=EvidenceType.SSDP, **signature), self.admin)

    def test_every_change_is_audited_with_old_and_new_values(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        acme.vendor_name = "Acme Corp"
        self.vendors.store(acme, self.admin)
        self.vendors.retire(acme.pk, self.admin)
        self.vendors.retire(acme.pk, self.admin)                          # already retired: no entry
        entries = self.audit_entries("vendor")
        self.assertEqual([entry.action.value for entry in entries], ["insert", "update", "update"])
        self.assertEqual(entries[1].old_value["vendor_name"], "Acme")
        self.assertEqual(entries[2].new_value["status"], "retired")
        self.assertTrue(all(entry.user_id == self.admin for entry in entries))

    def test_updating_a_missing_record_saves_nothing(self):
        with self.assertRaises(RecordNotFoundError):
            self.vendors.store(Vendor(vendor_id=999_999, vendor_name="Ghost"), self.admin)
        self.assertEqual(self.audit_entries("vendor"), [])

    def test_only_reference_kinds_are_accepted(self):
        with self.assertRaises(TypeError):
            ReferenceStore(self.engine, Device)

    # ---- the built-in "unknown" rows --------------------------------------------------

    def test_unknown_vendor_and_type_cannot_be_changed(self):
        cases = {
            "rename unknown vendor": lambda: self.vendors.store(Vendor(vendor_id=1, vendor_name="Apple"), self.admin),
            "retire unknown vendor": lambda: self.vendors.retire(1, self.admin),
            "retire unknown type":   lambda: self.types.retire(1, self.admin),
            "prefix for unknown":    lambda: self.prefixes.add("28:6F:B9", 1, self.admin),
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(ProtectedReferenceError):
                    change()

    # ---- OUI prefixes ------------------------------------------------------------------

    def test_prefix_can_be_reassigned_to_another_vendor(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        other = self.vendors.store(Vendor(vendor_name="Other Co"), self.admin)
        self.prefixes.add("28:6F:B9", acme.pk, self.admin)
        self.prefixes.reassign("28:6f:b9", other.pk, self.admin)
        self.assertEqual(self.prefixes.find("28:6F:B9").vendor_id, other.pk)
        self.assertEqual([e.action.value for e in self.audit_entries("oui_prefix")], ["insert", "update"])

    def test_prefix_for_a_missing_vendor_is_refused(self):
        with self.assertRaises(RecordNotFoundError):
            self.prefixes.add("28:6F:B9", 999_999, self.admin)

    # ---- 3.2.1 Find Reference Rules: only active records ----------------------------

    def test_retired_records_are_left_out_of_the_rules(self):
        acme = self.vendors.store(Vendor(vendor_name="Acme"), self.admin)
        old = self.vendors.store(Vendor(vendor_name="Old Co"), self.admin)
        self.prefixes.add("28:6F:B9", acme.pk, self.admin)
        self.prefixes.add("11:22:33", old.pk, self.admin)
        speaker = self.types.store(DeviceType(category_name="smart speaker"), self.admin)
        pager = self.types.store(DeviceType(category_name="pager"), self.admin)
        self.signatures.store(DeviceSignature(device_type_id=speaker.pk, pattern_value="echo",
                                              evidence_type=EvidenceType.MDNS), self.admin)
        self.signatures.store(DeviceSignature(device_type_id=pager.pk, pattern_value="pager",
                                              evidence_type=EvidenceType.MDNS), self.admin)
        self.vendors.retire(old.pk, self.admin)
        self.types.retire(pager.pk, self.admin)

        rules = find_reference_rules(self.engine)
        self.assertNotIn(old.pk, rules.vendors)
        self.assertIsNone(rules.vendor_for_prefix("11:22:33"))           # retired vendor's prefix
        self.assertEqual(sorted(t.category_name for t in rules.device_types.values()),
                         ["smart speaker", "unknown"])
        self.assertEqual([s.pattern_value for s in rules.signatures], ["echo"])   # pager's signature dropped


if __name__ == "__main__":
    unittest.main()
