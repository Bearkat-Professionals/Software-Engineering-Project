"""Unit tests for SH-4 Save Device Record, with fake 3.1 and 3.2 stores (no database).

    .\\dev test -k save_device -v
"""
from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime

from backend.records import Device, DeviceType, Vendor
from backend.records.enums import AuditAction, IdentificationLevel, NetworkStatus, RecordStatus
from backend.shared_modules.save_device_record import (DeviceChanges, DeviceRecordSaver, SaveOutcome,
                                                       SaveStatus, UNKNOWN_VENDOR_ID, provisional_level)
from tests.fakes.fake_device_store import FakeDeviceStore
from tests.fakes.fake_reference_store import FakeReferenceStore

USER = 7
UNIT = "unit-1"                         # any object works as the unit of work in the fakes
FIRST = datetime(2026, 10, 29, 12, 0, 0)
LATER = datetime(2026, 10, 29, 13, 0, 0)
ROUTER_MAC = "70:13:01:65:8E:F7"        # vendor-assigned
PHONE_MAC = "DA:A1:19:00:00:01"         # randomized (second digit A)
OTHER_PHONE_MAC = "DA:A1:19:00:00:02"   # randomized

APPLE_VENDOR, USER_VENDOR, RETIRED_VENDOR = 9, 42, 13
ROUTER_TYPE, RETIRED_TYPE = 5, 6
VENDORS = [
    Vendor(vendor_id=APPLE_VENDOR, vendor_name="Apple, Inc."),
    Vendor(vendor_id=USER_VENDOR, vendor_name="Vantiva"),
    Vendor(vendor_id=RETIRED_VENDOR, vendor_name="Old Co", status=RecordStatus.RETIRED),
]
DEVICE_TYPES = [
    DeviceType(device_type_id=ROUTER_TYPE, category_name="Router"),
    DeviceType(device_type_id=RETIRED_TYPE, category_name="Pager", status=RecordStatus.RETIRED),
]


def sighting(mac: str, ip: str = "192.168.1.20", seen_at: datetime = FIRST, **fields: object) -> DeviceChanges:
    """What a scan sends for one host."""
    return DeviceChanges(mac_address=mac, current_ip=ip, seen_at=seen_at, **fields)


class SaverTestCase(unittest.TestCase):
    """Every test starts with an empty device store and the vendors and types above."""

    def setUp(self) -> None:
        self.use()

    def use(self, *existing: Device) -> None:
        """Start from these devices already being stored."""
        self.devices = FakeDeviceStore(existing)
        self.vendors = FakeReferenceStore(Vendor, VENDORS)
        self.device_types = FakeReferenceStore(DeviceType, DEVICE_TYPES)
        self.saver = DeviceRecordSaver(self.devices, self.vendors, self.device_types)

    def save(self, changes: DeviceChanges,
             level: IdentificationLevel = IdentificationLevel.UNIDENTIFIED) -> SaveOutcome:
        return self.saver.save(changes, acting_user_id=USER, level=level, unit=UNIT)

    def actions(self) -> list[AuditAction]:
        return [entry.action for entry in self.devices.audit]

    def assert_rejected(self, changes: DeviceChanges, expected_in_reason: str) -> SaveOutcome:
        """Rejected with a reason containing the text, and nothing stored or audited (OUT-04)."""
        before = deepcopy(self.devices.rows)
        outcome = self.save(changes)
        self.assertEqual(outcome.status, SaveStatus.REJECTED)
        self.assertIsNone(outcome.device)
        self.assertFalse(outcome.flagged)
        self.assertTrue(any(expected_in_reason in reason for reason in outcome.reasons), outcome.reasons)
        self.assertEqual(self.devices.rows, before)
        self.assertEqual(self.devices.audit, [])
        return outcome


class NewDeviceTests(SaverTestCase):
    def test_new_mac_is_created_seen_online_and_flagged(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC, "192.168.1.1"))

        self.assertEqual(outcome.status, SaveStatus.CREATED)
        device = outcome.device
        self.assertEqual((device.mac_address, device.current_ip), (ROUTER_MAC, "192.168.1.1"))
        self.assertEqual((device.first_seen, device.last_seen), (FIRST, FIRST))
        self.assertEqual(device.network_status, NetworkStatus.ONLINE)
        self.assertTrue(outcome.flagged)
        self.assertEqual(self.actions(), [AuditAction.INSERT])
        self.assertEqual(self.devices.audit[0].changed_by, USER)

    def test_new_device_gets_the_unknown_vendor_and_type(self) -> None:     # DI-03
        outcome = self.save(sighting(ROUTER_MAC))
        self.assertEqual((outcome.device.vendor_id, outcome.device.device_type_id), (1, 1))


class SameMacTests(SaverTestCase):
    """FR-04, AT-02: an IP change updates the same device, never a second one."""

    def test_same_mac_at_a_new_ip_updates_the_same_device(self) -> None:
        first = self.save(sighting(ROUTER_MAC, "192.168.1.5", FIRST))
        second = self.save(sighting(ROUTER_MAC, "192.168.1.9", LATER))

        self.assertEqual(second.status, SaveStatus.UPDATED)
        self.assertEqual(second.device.pk, first.device.pk)
        self.assertEqual(len(self.devices.rows), 1)
        self.assertEqual(second.device.current_ip, "192.168.1.9")
        self.assertEqual((second.device.first_seen, second.device.last_seen), (FIRST, LATER))
        self.assertEqual(self.actions(), [AuditAction.INSERT, AuditAction.UPDATE])

    def test_mac_in_another_format_finds_the_same_device(self) -> None:
        self.save(sighting(ROUTER_MAC))
        outcome = self.save(sighting("70-13-01-65-8e-f7", seen_at=LATER))
        self.assertEqual(outcome.status, SaveStatus.UPDATED)
        self.assertEqual(outcome.device.mac_address, ROUTER_MAC)
        self.assertEqual(len(self.devices.rows), 1)

    def test_fields_left_as_none_keep_what_is_stored(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, device_name="Living room router",
                        model="AX3000", hostname="router.lan"))
        outcome = self.save(sighting(ROUTER_MAC, seen_at=LATER))           # a scan sends no name or model
        self.assertEqual((outcome.device.device_name, outcome.device.model, outcome.device.hostname),
                         ("Living room router", "AX3000", "router.lan"))


class RandomizedMacTests(SaverTestCase):
    """DI-01, DI-02, AT-03, and PR note 1: the flag comes from the MAC, never the caller."""

    def test_randomized_is_worked_out_from_the_mac(self) -> None:
        phone = self.save(sighting(PHONE_MAC))
        router = self.save(sighting(ROUTER_MAC, "192.168.1.1"))
        self.assertTrue(phone.device.is_randomized_mac)
        self.assertFalse(router.device.is_randomized_mac)

    def test_a_caller_cannot_pass_is_randomized_mac(self) -> None:
        with self.assertRaises(TypeError):
            DeviceChanges(mac_address=ROUTER_MAC, is_randomized_mac=True)    # the field no longer exists

    def test_vendor_mac_saved_twice_is_one_device(self) -> None:           # the reviewer's case
        self.save(sighting(ROUTER_MAC, "192.168.1.5"))
        self.save(sighting(ROUTER_MAC, "192.168.1.9", LATER))
        self.assertEqual(len(self.devices.rows), 1)
        self.assertFalse(self.devices.rows[1].is_randomized_mac)

    def test_same_randomized_mac_again_updates_that_device(self) -> None:
        first = self.save(sighting(PHONE_MAC))
        again = self.save(sighting(PHONE_MAC, "192.168.1.31", LATER))
        self.assertEqual(again.status, SaveStatus.UPDATED)
        self.assertEqual(again.device.pk, first.device.pk)
        self.assertEqual(len(self.devices.rows), 1)

    def test_different_randomized_mac_creates_a_new_device(self) -> None:
        self.save(sighting(PHONE_MAC))
        other = self.save(sighting(OTHER_PHONE_MAC))
        self.assertEqual(other.status, SaveStatus.CREATED)
        self.assertEqual(len(self.devices.rows), 2)


class VendorTests(SaverTestCase):
    """Design 10.4: a scan's OUI match never replaces a vendor that is already known."""

    def test_oui_match_sets_the_vendor_of_a_new_device(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(outcome.device.vendor_id, APPLE_VENDOR)

    def test_oui_match_fills_in_an_unknown_vendor(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, vendor_id=UNKNOWN_VENDOR_ID))
        outcome = self.save(sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(outcome.device.vendor_id, APPLE_VENDOR)

    def test_user_set_vendor_is_not_replaced_by_a_scan(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, vendor_id=USER_VENDOR))        # corrected by a user
        outcome = self.save(sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(outcome.device.vendor_id, USER_VENDOR)

    def test_a_users_vendor_wins_over_an_oui_match_in_the_same_save(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR, vendor_id=USER_VENDOR))
        self.assertEqual(outcome.device.vendor_id, USER_VENDOR)


class ReferenceTests(SaverTestCase):
    """PR note 2: a vendor or type that doesn't exist, or is retired, is a REJECTED with a
    reason, not a database error (OUT-04, DI-06)."""

    def test_vendor_that_does_not_exist(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, vendor_id=999), "no vendor with id 999")

    def test_oui_vendor_that_does_not_exist(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, oui_vendor_id=999), "no vendor with id 999")

    def test_device_type_that_does_not_exist(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, device_type_id=999), "no device type with id 999")

    def test_retired_vendor_cannot_be_chosen(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, vendor_id=RETIRED_VENDOR), "is retired")

    def test_retired_type_cannot_be_chosen(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, device_type_id=RETIRED_TYPE), "is retired")

    def test_rejection_leaves_an_existing_device_unchanged(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, current_ip="192.168.1.5"))
        self.assert_rejected(sighting(ROUTER_MAC, "192.168.1.9", LATER, device_type_id=999), "no device type")
        self.assertEqual(self.devices.rows[1].current_ip, "192.168.1.5")

    def test_device_keeps_a_retired_vendor_it_already_has(self) -> None:   # DI-06: retire, don't rewrite
        self.use(Device(mac_address=ROUTER_MAC, vendor_id=RETIRED_VENDOR))
        outcome = self.save(sighting(ROUTER_MAC, vendor_id=RETIRED_VENDOR))
        self.assertEqual(outcome.status, SaveStatus.UPDATED)
        self.assertEqual(outcome.device.vendor_id, RETIRED_VENDOR)

    def test_valid_vendor_and_type_are_applied(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC, vendor_id=USER_VENDOR, device_type_id=ROUTER_TYPE))
        self.assertEqual((outcome.device.vendor_id, outcome.device.device_type_id), (USER_VENDOR, ROUTER_TYPE))

    def test_known_device_rescan_looks_nothing_up(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, vendor_id=APPLE_VENDOR))
        self.save(sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(self.vendors.lookups, [])

    def test_oui_match_that_will_not_apply_is_not_checked(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, vendor_id=USER_VENDOR))
        outcome = self.save(sighting(ROUTER_MAC, oui_vendor_id=999))
        self.assertEqual(outcome.status, SaveStatus.UPDATED)
        self.assertEqual(self.vendors.lookups, [])


class TextTests(SaverTestCase):
    """PR notes 2 and 3: text must fit its column, and is otherwise stored as typed."""

    def test_text_longer_than_its_column_is_rejected(self) -> None:
        for name in ("device_name", "hostname", "model"):
            with self.subTest(field=name):
                label = name.replace("_", " ")
                self.assert_rejected(sighting(ROUTER_MAC, **{name: "x" * 256}),
                                     f"The {label} is 256 characters long; the limit is 255")

    def test_text_that_exactly_fits_is_saved(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC, device_name="x" * 255))
        self.assertEqual(outcome.status, SaveStatus.CREATED)

    def test_text_is_stored_exactly_as_typed(self) -> None:   # screens escape it on display (XSS)
        typed = "<script>alert(1)</script>"
        outcome = self.save(sighting(ROUTER_MAC, device_name=typed))
        self.assertEqual(outcome.device.device_name, typed)


class FlagTests(SaverTestCase):
    """Spec 7.3 through SH-4.1, AT-05."""

    def test_trusted_device_stays_unflagged_after_a_scan(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, trusted_status=True))
        outcome = self.save(sighting(ROUTER_MAC, seen_at=LATER), IdentificationLevel.UNIDENTIFIED)
        self.assertFalse(outcome.flagged)
        self.assertFalse(self.devices.rows[outcome.device.pk].flagged_status)

    def test_marking_a_device_trusted_clears_its_flag(self) -> None:
        self.use(Device(mac_address=ROUTER_MAC, flagged_status=True))
        outcome = self.save(DeviceChanges(mac_address=ROUTER_MAC, trusted_status=True))
        self.assertFalse(outcome.flagged)

    def test_trusted_in_the_same_save_as_a_new_device_is_still_flagged(self) -> None:    # rule 3
        outcome = self.save(sighting(ROUTER_MAC, trusted_status=True))
        self.assertTrue(outcome.device.trusted_status)
        self.assertTrue(outcome.flagged)

    def test_identified_device_is_not_flagged(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC), IdentificationLevel.IDENTIFIED)
        self.assertFalse(outcome.flagged)

    def test_partial_device_is_flagged(self) -> None:
        outcome = self.save(sighting(ROUTER_MAC), IdentificationLevel.PARTIAL)
        self.assertTrue(outcome.flagged)


class ValueRejectionTests(SaverTestCase):
    """OUT-04: values that are wrong on their own are rejected before the store is called."""

    def test_invalid_mac(self) -> None:
        self.assert_rejected(sighting("not a mac"), "not a valid MAC address")

    def test_score_of_101(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, confidence_score=101), "Confidence scores go from 0 to 100")

    def test_invalid_ip(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, "192.168.1.300"), "Invalid IP address")

    def test_sighting_without_an_ip(self) -> None:
        self.assert_rejected(DeviceChanges(mac_address=ROUTER_MAC, seen_at=FIRST), "needs the IP address")

    def test_every_reason_is_listed(self) -> None:
        outcome = self.assert_rejected(sighting("bad", "bad", confidence_score=-1, model="x" * 300), "MAC")
        self.assertEqual(len(outcome.reasons), 4)

    def test_value_problems_never_reach_the_store(self) -> None:
        self.save(sighting("not a mac", device_name="x" * 300))
        self.assertEqual(self.devices.units_seen, [])
        self.assertEqual(self.vendors.lookups, [])


class UnitOfWorkTests(SaverTestCase):
    def test_find_and_store_both_use_the_callers_unit(self) -> None:
        self.save(sighting(ROUTER_MAC))
        self.assertEqual(self.devices.units_seen, [UNIT, UNIT])
        self.assertEqual(self.devices.audit[0].unit, UNIT)


class ProvisionalLevelTests(unittest.TestCase):
    def test_known_vendor_is_partial(self) -> None:
        self.assertEqual(provisional_level(APPLE_VENDOR), IdentificationLevel.PARTIAL)

    def test_no_or_unknown_vendor_is_unidentified(self) -> None:
        for vendor_id in (None, UNKNOWN_VENDOR_ID):
            with self.subTest(vendor_id=vendor_id):
                self.assertEqual(provisional_level(vendor_id), IdentificationLevel.UNIDENTIFIED)


if __name__ == "__main__":
    unittest.main()
