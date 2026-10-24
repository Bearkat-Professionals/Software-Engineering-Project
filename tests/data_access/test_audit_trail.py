"""Integration tests for the audit trail (#14, section 3.4).

Same TEST database setup as test_repository.py; skipped if it is not available.

    python -m unittest discover tests -v
"""
import unittest
from dataclasses import replace

from backend.data_access.audit_trail import AuditTrail
from backend.data_access.database_engine import IntegrityConstraintError
from backend.data_access.repository import Repository
from backend.records import Device, User, Vendor
from backend.records.enums import AuditAction
# Only the helpers are imported, not RepositoryTests, so those tests don't run twice
from tests.data_access.test_repository import CLEANUP, connect_to_test_database


class AuditTrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()
        cls.audit = AuditTrail(cls.engine)
        cls.vendors = Repository(cls.engine, Vendor)
        cls.devices = Repository(cls.engine, Device)
        cls.users = Repository(cls.engine, User)

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        # every audit entry needs a real user (FK -> app_user)
        self.user = self.users.create(User(username="auditor", password_hash="not-a-real-hash"))

    def _create_vendor(self, name: str) -> Vendor:
        with self.engine.unit_of_work() as unit:
            vendor = self.vendors.create(Vendor(vendor_name=name), unit)
            self.audit.record(self.user.user_id, AuditAction.INSERT, None, vendor, unit)
        return vendor

    # ---- one entry per kind of change ---------------------------------------

    def test_insert_is_recorded_with_new_value_only(self):
        vendor = self._create_vendor("Acme Networks")
        [entry] = self.audit.history("vendor", vendor.vendor_id)
        self.assertEqual(entry.action, AuditAction.INSERT)      # text came back as the enum
        self.assertEqual(entry.user_id, self.user.user_id)
        self.assertIsNone(entry.old_value)
        self.assertEqual(entry.new_value["vendor_name"], "Acme Networks")   # JSON came back as a dict
        self.assertIsNotNone(entry.changed_at)

    def test_update_is_recorded_with_old_and_new_values(self):
        vendor = self._create_vendor("Acme Networks")
        with self.engine.unit_of_work() as unit:
            old = self.vendors.get(vendor.vendor_id, unit)
            changed = replace(old, vendor_name="Acme Corp")
            self.vendors.update(changed, unit)
            self.audit.record(self.user.user_id, AuditAction.UPDATE, old, changed, unit)

        entry = self.audit.latest("vendor", vendor.vendor_id)
        self.assertEqual(entry.action, AuditAction.UPDATE)
        self.assertEqual(entry.old_value["vendor_name"], "Acme Networks")
        self.assertEqual(entry.new_value["vendor_name"], "Acme Corp")

    def test_delete_is_recorded_with_old_value_only(self):
        vendor = self._create_vendor("Short Lived Co")
        with self.engine.unit_of_work() as unit:
            old = self.vendors.get(vendor.vendor_id, unit)
            self.vendors.delete(vendor.vendor_id, unit)
            self.audit.record(self.user.user_id, AuditAction.DELETE, old, None, unit)

        entry = self.audit.latest("vendor", vendor.vendor_id)
        self.assertEqual(entry.action, AuditAction.DELETE)
        self.assertEqual(entry.old_value["vendor_name"], "Short Lived Co")
        self.assertIsNone(entry.new_value)

    # ---- change and audit entry are one transaction ------------------------

    def test_if_the_change_fails_no_audit_entry_is_stored(self):
        self._create_vendor("Duplicate Co")
        with self.assertRaises(IntegrityConstraintError):
            with self.engine.unit_of_work() as unit:
                other = self.vendors.create(Vendor(vendor_name="Other Co"), unit)
                self.audit.record(self.user.user_id, AuditAction.INSERT, None, other, unit)
                self.vendors.create(Vendor(vendor_name="Duplicate Co"), unit)   # UNIQUE vendor_name
        self.assertEqual(len(self.audit.by_user(self.user.user_id)), 1)   # only the first vendor's entry

    def test_record_requires_a_unit_of_work(self):
        vendor = self._create_vendor("Acme Networks")
        with self.assertRaises(ValueError):
            self.audit.record(self.user.user_id, AuditAction.UPDATE, vendor, vendor, None)

    def test_unsaved_record_is_rejected(self):
        with self.assertRaises(ValueError):
            with self.engine.unit_of_work() as unit:
                self.audit.record(self.user.user_id, AuditAction.INSERT,
                                  None, Vendor(vendor_name="Never Saved"), unit)

    # ---- reading the trail ---------------------------------------------------

    def test_latest_returns_the_newest_entry_or_none(self):
        self.assertIsNone(self.audit.latest("vendor", 999_999))
        vendor = self._create_vendor("Acme Networks")
        for name in ("Acme Corp", "Acme Inc"):
            with self.engine.unit_of_work() as unit:
                old = self.vendors.get(vendor.vendor_id, unit)
                changed = replace(old, vendor_name=name)
                self.vendors.update(changed, unit)
                self.audit.record(self.user.user_id, AuditAction.UPDATE, old, changed, unit)
        self.assertEqual(self.audit.latest("vendor", vendor.vendor_id).new_value["vendor_name"], "Acme Inc")
        self.assertEqual(len(self.audit.history("vendor", vendor.vendor_id)), 3)

    def test_by_user_only_returns_that_users_entries(self):
        other_user = self.users.create(User(username="someone_else", password_hash="not-a-real-hash"))
        self._create_vendor("Mine Co")
        with self.engine.unit_of_work() as unit:
            theirs = self.vendors.create(Vendor(vendor_name="Theirs Co"), unit)
            self.audit.record(other_user.user_id, AuditAction.INSERT, None, theirs, unit)
        mine = self.audit.by_user(self.user.user_id)
        self.assertEqual([e.new_value["vendor_name"] for e in mine], ["Mine Co"])

    def test_record_loaded_from_database_can_be_snapshotted(self):
        # A loaded row carries DB-filled values (dates, enums); the snapshot must still be valid JSON
        device = self.devices.create(Device(mac_address="AA:BB:CC:00:00:01"))
        with self.engine.unit_of_work() as unit:
            loaded = self.devices.get(device.device_id, unit)
            self.audit.record(self.user.user_id, AuditAction.UPDATE, loaded, loaded, unit)
        entry = self.audit.latest("device", device.device_id)
        self.assertEqual(entry.new_value["mac_address"], "AA:BB:CC:00:00:01")

    def test_trail_cannot_be_edited(self):
        self.assertFalse(hasattr(self.audit, "update"))
        self.assertFalse(hasattr(self.audit, "delete"))


if __name__ == "__main__":
    unittest.main()
