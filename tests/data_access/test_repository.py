"""Integration tests for the generic repository (#13).

These run against a separate TEST database, never the real one. One-time setup:
create network_device_scanner_test, then run the schema file and the seed file
on it (same steps as the real database). The name is DB_NAME + "_test" unless
TEST_DB_NAME is set in .env.

Without that database (or without credentials) these tests are skipped, so the
rest of the suite still runs.

    python -m unittest discover tests
    python -m unittest discover tests -v (verbose)

    or

    py -m unittest discover tests
    py -m unittest discover tests -v (verbose)

"""
import os
import unittest
from dataclasses import replace

from backend.config import DatabaseConfig
from backend.data_access.database_engine import (DatabaseConnectionError, DatabaseEngine,
                                                 IntegrityConstraintError)
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import AlertSetting, Device, User, Vendor
from backend.records.enums import AlertEventName, RecordStatus

# Everything a test may create, children before parents (foreign keys).
# Seed rows are kept: vendor 1 / device type 1 ("unknown") and the alert events.
CLEANUP = (
    "DELETE FROM identification_evidence",
    "DELETE FROM scan_observation",
    "DELETE FROM device",
    "DELETE FROM oui_prefix",
    "DELETE FROM vendor WHERE vendor_id <> 1",
    "DELETE FROM alert_setting",            # its alert_setting_event links cascade
    "DELETE FROM audit_log",
    "DELETE FROM scan",
    "DELETE FROM app_user",
)


def connect_to_test_database() -> DatabaseEngine:
    try:
        config = DatabaseConfig.from_env()
    except ValueError as exc:
        raise unittest.SkipTest(f"no database credentials: {exc}")

    name = os.getenv("TEST_DB_NAME", f"{config.database}_test")
    if not name.endswith("_test"):
        # setUp deletes rows, so never let these tests near a real database
        raise RuntimeError(f"Refusing to run tests against '{name}': the name must end in _test")

    try:
        return DatabaseEngine(replace(config, database=name), pool_size=2)
    except DatabaseConnectionError as exc:
        raise unittest.SkipTest(f"test database not available: {exc}")


class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Runs once for the whole class: one connection pool shared by every test
        cls.engine = connect_to_test_database()
        cls.vendors = Repository(cls.engine, Vendor)
        cls.devices = Repository(cls.engine, Device)
        cls.users = Repository(cls.engine, User)
        cls.alert_settings = Repository(cls.engine, AlertSetting)

    def setUp(self):
        # Runs before every test: start from an empty database
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)

    # ---- the ticket's "Done when" checks ----------------------------------

    def test_vendor_can_be_created_loaded_changed_and_deleted(self):
        vendor = self.vendors.create(Vendor(vendor_name="Acme Networks"))
        self.assertIsNotNone(vendor.vendor_id)                  # the new id was filled in

        loaded = self.vendors.get(vendor.vendor_id)
        self.assertEqual(loaded.vendor_name, "Acme Networks")
        self.assertEqual(loaded.status, RecordStatus.ACTIVE)    # text came back as the enum

        loaded.vendor_name = "Acme Corp"
        self.vendors.update(loaded)
        self.assertEqual(self.vendors.get(vendor.vendor_id).vendor_name, "Acme Corp")

        self.vendors.delete(vendor.vendor_id)
        self.assertIsNone(self.vendors.get(vendor.vendor_id))

    def test_if_the_second_of_two_saves_fails_neither_is_stored(self):
        with self.assertRaises(IntegrityConstraintError):
            with self.engine.unit_of_work() as unit:
                self.vendors.create(Vendor(vendor_name="Duplicate Co"), unit)
                self.vendors.create(Vendor(vendor_name="Duplicate Co"), unit)   # UNIQUE vendor_name
        self.assertEqual(self.vendors.list(vendor_name="Duplicate Co"), [])    # the first one was rolled back too

    # ---- other behaviour the data-access modules will rely on --------------

    def test_update_with_no_changes_still_finds_the_row(self):
        vendor = self.vendors.create(Vendor(vendor_name="Unchanged Co"))
        self.vendors.update(vendor)          # would raise RecordNotFoundError without FOUND_ROWS

    def test_missing_records(self):
        self.assertIsNone(self.vendors.get(999_999))
        with self.assertRaises(RecordNotFoundError):
            self.vendors.update(Vendor(vendor_id=999_999, vendor_name="Ghost Co"))
        with self.assertRaises(RecordNotFoundError):
            self.vendors.delete(999_999)

    def test_list_filters_by_column(self):
        self.vendors.create(Vendor(vendor_name="Active Co"))
        self.vendors.create(Vendor(vendor_name="Retired Co", status=RecordStatus.RETIRED))
        retired = self.vendors.list(status=RecordStatus.RETIRED)
        self.assertEqual([v.vendor_name for v in retired], ["Retired Co"])
        with self.assertRaises(ValueError):
            self.vendors.list(no_such_column=1)

    def test_device_without_type_or_vendor_gets_the_unknown_rows(self):     # DI-03
        device = self.devices.create(Device(mac_address="AA:BB:CC:00:00:01"))
        loaded = self.devices.get(device.device_id)
        self.assertEqual(loaded.device_type_id, 1)
        self.assertEqual(loaded.vendor_id, 1)

    def test_same_fixed_mac_twice_is_rejected_but_randomized_is_not(self):  # DI-01, DI-02
        self.devices.create(Device(mac_address="AA:BB:CC:00:00:02"))
        with self.assertRaises(IntegrityConstraintError):
            self.devices.create(Device(mac_address="AA:BB:CC:00:00:02"))
        self.devices.create(Device(mac_address="DA:00:00:00:00:03", is_randomized_mac=True))
        self.devices.create(Device(mac_address="DA:00:00:00:00:03", is_randomized_mac=True))

    def test_vendor_in_use_cannot_be_deleted(self):                         # DI-06
        vendor = self.vendors.create(Vendor(vendor_name="In Use Co"))
        self.devices.create(Device(mac_address="AA:BB:CC:00:00:04", vendor_id=vendor.vendor_id))
        with self.assertRaises(IntegrityConstraintError):
            self.vendors.delete(vendor.vendor_id)

    def test_child_table_field_is_left_alone(self):
        user = self.users.create(User(username="tester", password_hash="not-a-real-hash"))
        setting = self.alert_settings.create(AlertSetting(
            user_id=user.user_id, recipient_email="tester@example.com",
            events=[AlertEventName.SCAN_FAILED]))
        loaded = self.alert_settings.get(setting.alert_setting_id)
        self.assertEqual(loaded.recipient_email, "tester@example.com")
        self.assertEqual(loaded.events, [])   # events live in alert_setting_event; #17 will read and write them


if __name__ == "__main__":
    unittest.main()