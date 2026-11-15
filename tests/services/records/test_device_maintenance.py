"""Tests for 2.3.1 Device Record Maintenance with fakes: no database.

The real SH-4 runs on fake stores (SpySaver records what it was asked), so these
also show SH-4 and the service agree.

    py -m unittest tests.services.records.test_device_maintenance -v
"""
from __future__ import annotations

import unittest
from datetime import timedelta

from backend.records import Device, DeviceType, Vendor
from backend.records.enums import AuditAction, IdentificationLevel, NetworkStatus, RecordStatus
from backend.services.records.device_maintenance import (MARKS, DeviceEdit, DeviceMaintenance, LoadedDevice,
                                                         MaintenanceResult, MaintenanceStatus)
from tests.fakes.fake_device_maintenance import FakeClock, FakeEngine, FakeMaintainedDevices, SpySaver
from tests.fakes.fake_reference_store import FakeReferenceStore

ADMIN, OTHER = 7, 8
ROUTER_MAC = "70:13:01:65:8E:F7"        # fixed (vendor-assigned)
PHONE_MAC = "DA:A1:19:00:00:01"         # randomized
APPLE, RETIRED_VENDOR = 9, 13
ROUTER_TYPE = 5

DONE = MaintenanceStatus.DONE
REJECTED = MaintenanceStatus.REJECTED


class MaintenanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.use(Device(device_id=1, mac_address=ROUTER_MAC, vendor_id=APPLE, device_type_id=1,
                        device_name="Router", current_ip="192.168.1.1"))

    def use(self, *devices: Device) -> None:
        self.clock = FakeClock()
        self.engine = FakeEngine(self.clock)
        self.devices = FakeMaintainedDevices(devices, self.clock)
        vendors = FakeReferenceStore(Vendor, [Vendor(vendor_id=APPLE, vendor_name="Apple, Inc."),
                                              Vendor(vendor_id=RETIRED_VENDOR, vendor_name="Old Co",
                                                     status=RecordStatus.RETIRED)])
        types = FakeReferenceStore(DeviceType, [DeviceType(device_type_id=ROUTER_TYPE, category_name="Router")])
        self.saver = SpySaver(self.devices, vendors, types)
        self.service = DeviceMaintenance(self.engine, self.saver, self.devices)

    def assertStatus(self, result: MaintenanceResult, status: MaintenanceStatus, *reason_parts: str) -> None:
        self.assertIsInstance(result, MaintenanceResult)
        self.assertEqual(result.status, status, result.reasons)
        if status is DONE:
            self.assertEqual(result.reasons, ())
        else:
            self.assertTrue(result.reasons, "a refusal needs a reason a person can read")
            self.assertIsNone(result.device)
        for part in reason_parts:
            self.assertTrue(any(part in reason for reason in result.reasons), (part, result.reasons))

    def assertNothingStored(self) -> None:
        self.assertEqual(self.devices.audit, [])

    def assertOneUnitOfWork(self) -> None:
        """Everything ran in the one unit the method opened (CM-04)."""
        self.assertEqual(self.engine.opened, 1)
        self.assertEqual({repr(unit) for unit in self.devices.units_seen}, {"unit-1"})
        self.assertEqual({repr(call.unit) for call in self.saver.calls}, {"unit-1"} if self.saver.calls else set())


# ---- add ------------------------------------------------------------------------

class AddTests(MaintenanceTestCase):
    def test_a_new_device_starts_offline_and_unseen(self) -> None:
        result = self.service.add("3c:07:54:00:00:09", DeviceEdit(device_name="Den TV"), ADMIN, ip="192.168.1.50")
        self.assertStatus(result, DONE)
        device = result.device
        self.assertEqual((device.mac_address, device.current_ip, device.device_name),
                         ("3C:07:54:00:00:09", "192.168.1.50", "Den TV"))
        self.assertEqual(device.network_status, NetworkStatus.OFFLINE)
        self.assertEqual((device.first_seen, device.last_seen), (None, None))
        self.assertEqual([(entry.action, entry.changed_by) for entry in self.devices.audit],
                         [(AuditAction.INSERT, ADMIN)])
        self.assertEqual((result.changed_by, result.changed_at), (ADMIN, self.clock()))
        self.assertOneUnitOfWork()
        self.assertEqual(self.engine.committed, 1)

    def test_add_trusted(self) -> None:
        result = self.service.add("3C:07:54:00:00:09", DeviceEdit(), ADMIN, trusted=True)
        self.assertStatus(result, DONE)
        self.assertTrue(result.device.trusted_status)

    def test_add_uses_the_provisional_level_of_the_vendor_it_is_given(self) -> None:
        self.service.add("3C:07:54:00:00:09", DeviceEdit(vendor_id=APPLE), ADMIN)
        self.service.add("3C:07:54:00:00:0A", DeviceEdit(), ADMIN)
        self.assertEqual([call.level for call in self.saver.calls],
                         [IdentificationLevel.PARTIAL, IdentificationLevel.UNIDENTIFIED])

    def test_a_known_fixed_mac_already_exists(self) -> None:
        result = self.service.add("70-13-01-65-8e-f7", DeviceEdit(device_name="Again"), ADMIN)
        self.assertStatus(result, MaintenanceStatus.ALREADY_EXISTS, "70:13:01:65:8E:F7 is already stored")
        self.assertEqual(result.existing_device_id, 1)
        self.assertNothingStored()
        self.assertEqual(self.saver.calls, [])

    def test_a_known_randomized_mac_already_exists(self) -> None:
        self.use(Device(device_id=4, mac_address=PHONE_MAC, is_randomized_mac=True))
        result = self.service.add(PHONE_MAC.lower(), DeviceEdit(), ADMIN)
        self.assertStatus(result, MaintenanceStatus.ALREADY_EXISTS)
        self.assertEqual(result.existing_device_id, 4)

    def test_fixed_and_randomized_are_looked_up_separately(self) -> None:      # DI-02
        self.use(Device(device_id=4, mac_address=PHONE_MAC, is_randomized_mac=False))   # an old fixed row
        result = self.service.add(PHONE_MAC, DeviceEdit(), ADMIN)                       # this MAC is randomized
        self.assertStatus(result, DONE)
        self.assertTrue(result.device.is_randomized_mac)
        self.assertNotEqual(result.device.pk, 4)

    def test_a_bad_mac_or_ip_is_rejected_by_sh4(self) -> None:
        result = self.service.add("not-a-mac", DeviceEdit(), ADMIN, ip="999.1.1.1")
        self.assertStatus(result, REJECTED)
        self.assertEqual(len(result.reasons), 2)                # both reported at once (OUT-04)
        self.assertNothingStored()

    def test_a_retired_vendor_is_rejected(self) -> None:
        result = self.service.add("3C:07:54:00:00:09", DeviceEdit(vendor_id=RETIRED_VENDOR), ADMIN)
        self.assertStatus(result, REJECTED, "retired")
        self.assertNothingStored()


# ---- edit ------------------------------------------------------------------------

class EditTests(MaintenanceTestCase):
    def test_each_field_is_passed_to_sh4_with_loaded_at(self) -> None:
        loaded_at = self.clock()
        edit = DeviceEdit(device_name="Main router", device_type_id=ROUTER_TYPE, vendor_id=APPLE, model="RT-AX88U")
        result = self.service.edit(1, edit, ADMIN, loaded_at)
        self.assertStatus(result, DONE)

        call = self.saver.calls[0]
        self.assertEqual(call.changes.mac_address, ROUTER_MAC)          # found by id, saved by its MAC
        self.assertEqual((call.changes.device_name, call.changes.device_type_id, call.changes.vendor_id,
                          call.changes.model), ("Main router", ROUTER_TYPE, APPLE, "RT-AX88U"))
        self.assertIsNone(call.changes.seen_at)                         # an edit is not a sighting
        self.assertEqual(call.loaded_at, loaded_at)
        self.assertEqual((result.device.device_name, result.device.model), ("Main router", "RT-AX88U"))
        self.assertEqual([entry.action for entry in self.devices.audit], [AuditAction.UPDATE])
        self.assertOneUnitOfWork()

    def test_fields_left_out_stay_as_they_are(self) -> None:
        result = self.service.edit(1, DeviceEdit(model="X1"), ADMIN, None)
        self.assertEqual((result.device.device_name, result.device.vendor_id), ("Router", APPLE))

    def test_a_blank_name_clears_it(self) -> None:
        result = self.service.edit(1, DeviceEdit(device_name="  "), ADMIN, None)
        self.assertIsNone(result.device.device_name)

    def test_the_level_follows_the_vendor_after_the_edit(self) -> None:
        self.service.edit(1, DeviceEdit(model="X"), ADMIN, None)
        self.assertEqual(self.saver.calls[-1].level, IdentificationLevel.PARTIAL)          # keeps Apple
        self.service.edit(1, DeviceEdit(vendor_id=1), ADMIN, None)
        self.assertEqual(self.saver.calls[-1].level, IdentificationLevel.UNIDENTIFIED)     # back to unknown

    def test_not_found(self) -> None:
        result = self.service.edit(99, DeviceEdit(model="X"), ADMIN, None)
        self.assertStatus(result, MaintenanceStatus.NOT_FOUND, "no device with id 99")
        self.assertEqual(self.saver.calls, [])

    def test_an_empty_edit(self) -> None:
        self.assertStatus(self.service.edit(1, DeviceEdit(), ADMIN, None), REJECTED, "Nothing to change")
        self.assertEqual(self.engine.opened, 0)

    def test_changed_since_loading_is_a_conflict_with_who_and_when(self) -> None:
        loaded_at = self.clock()
        later = loaded_at + timedelta(minutes=5)
        self.devices.changed_elsewhere(1, OTHER, later)
        result = self.service.edit(1, DeviceEdit(model="X"), ADMIN, loaded_at)
        self.assertStatus(result, MaintenanceStatus.CONFLICT, "changed by", later.strftime("%H:%M"))
        self.assertEqual((result.changed_by, result.changed_at), (OTHER, later))
        self.assertNothingStored()

    def test_your_own_last_change_is_not_a_conflict(self) -> None:
        first = self.service.edit(1, DeviceEdit(model="A"), ADMIN, self.clock())
        self.clock.advance(1)
        second = self.service.edit(1, DeviceEdit(model="B"), ADMIN, first.changed_at)
        self.assertStatus(second, DONE)

    def test_a_device_sharing_a_randomized_mac_with_a_newer_one_is_refused(self) -> None:   # DI-02
        self.use(Device(device_id=4, mac_address=PHONE_MAC, is_randomized_mac=True),
                 Device(device_id=5, mac_address=PHONE_MAC, is_randomized_mac=True))
        result = self.service.edit(4, DeviceEdit(model="X"), ADMIN, None)
        self.assertStatus(result, REJECTED, "shares this randomized MAC")
        self.assertNothingStored()
        self.assertStatus(self.service.edit(5, DeviceEdit(model="X"), ADMIN, None), DONE)  # the newest is fine


# ---- marks -------------------------------------------------------------------------

class MarkTests(MaintenanceTestCase):
    def test_each_mark_sets_the_right_fields(self) -> None:
        for mark, (trusted, flagged) in MARKS.items():
            with self.subTest(mark=mark):
                self.setUp()
                result = self.service.set_mark(1, mark, ADMIN, self.clock())
                self.assertStatus(result, DONE)
                call = self.saver.calls[0]
                self.assertEqual((call.changes.trusted_status, call.changes.flag_set_by_user), (trusted, flagged))
                self.assertEqual((result.device.trusted_status, result.device.flag_set_by_user), (trusted, flagged))
                self.assertEqual(call.loaded_at, self.clock())
                self.assertOneUnitOfWork()

    def test_the_table(self) -> None:                          # #PARENT
        self.assertEqual(MARKS, {"trusted": (True, False), "flagged": (False, True), "automatic": (False, False)})

    def test_flagged_and_trusted_change_the_flag(self) -> None:
        self.assertTrue(self.service.set_mark(1, "flagged", ADMIN, None).device.flagged_status)
        self.assertFalse(self.service.set_mark(1, "trusted", ADMIN, None).device.flagged_status)

    def test_automatic_clears_both_marks(self) -> None:
        self.use(Device(device_id=1, mac_address=ROUTER_MAC, trusted_status=True))
        self.service.set_mark(1, "flagged", ADMIN, None)
        result = self.service.set_mark(1, "automatic", ADMIN, None)
        self.assertEqual((result.device.trusted_status, result.device.flag_set_by_user), (False, False))

    def test_an_unknown_mark(self) -> None:
        result = self.service.set_mark(1, "suspicious", ADMIN, None)
        self.assertStatus(result, REJECTED, "'suspicious' is not a mark", "trusted, flagged, automatic")
        self.assertEqual(self.engine.opened, 0)

    def test_not_found_and_conflict(self) -> None:
        self.assertStatus(self.service.set_mark(99, "trusted", ADMIN, None), MaintenanceStatus.NOT_FOUND)
        self.devices.changed_elsewhere(1, OTHER, self.clock() + timedelta(minutes=1))
        self.assertStatus(self.service.set_mark(1, "trusted", ADMIN, self.clock()), MaintenanceStatus.CONFLICT)
        self.assertNothingStored()


# ---- delete ------------------------------------------------------------------------

class DeleteTests(MaintenanceTestCase):
    def test_returns_the_removed_counts(self) -> None:
        self.devices.children[1] = {"observations_removed": 3, "evidence_removed": 2}
        result = self.service.delete(1, ADMIN, self.clock())
        self.assertStatus(result, DONE)
        self.assertEqual(result.removed, {"observations_removed": 3, "evidence_removed": 2})
        self.assertNotIn(1, self.devices.rows)
        self.assertEqual([(entry.action, entry.changed_by) for entry in self.devices.audit],
                         [(AuditAction.DELETE, ADMIN)])
        self.assertOneUnitOfWork()

    def test_not_found(self) -> None:
        result = self.service.delete(99, ADMIN, None)
        self.assertStatus(result, MaintenanceStatus.NOT_FOUND, "no device with id 99")
        self.assertEqual(self.engine.rolled_back, 1)

    def test_conflict_carries_who_and_when_and_deletes_nothing(self) -> None:
        later = self.clock() + timedelta(minutes=3)
        self.devices.changed_elsewhere(1, OTHER, later)
        result = self.service.delete(1, ADMIN, self.clock())
        self.assertStatus(result, MaintenanceStatus.CONFLICT, later.strftime("%H:%M"), "wasn't deleted")
        self.assertEqual((result.changed_by, result.changed_at), (OTHER, later))
        self.assertIn(1, self.devices.rows)
        self.assertEqual(self.engine.rolled_back, 1)


# ---- load ---------------------------------------------------------------------------

class LoadTests(MaintenanceTestCase):
    def test_the_device_and_the_database_time(self) -> None:
        loaded = self.service.load(1)
        self.assertIsInstance(loaded, LoadedDevice)
        self.assertTrue(loaded.found)
        self.assertEqual(loaded.device.mac_address, ROUTER_MAC)
        self.assertEqual(loaded.loaded_at, self.clock())
        self.assertOneUnitOfWork()

    def test_missing(self) -> None:
        loaded = self.service.load(99)
        self.assertFalse(loaded.found)
        self.assertIsNone(loaded.loaded_at)

    def test_load_then_edit_round_trip(self) -> None:
        loaded = self.service.load(1)
        self.clock.advance(1)
        self.assertStatus(self.service.edit(1, DeviceEdit(model="X"), ADMIN, loaded.loaded_at), DONE)


if __name__ == "__main__":
    unittest.main()