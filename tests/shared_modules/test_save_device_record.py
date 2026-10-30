"""Unit tests for SH-4 Save Device Record, with a fake 3.1 (no database).

    .\\dev test -k save_device
"""
from __future__ import annotations

import unittest
from datetime import datetime

from backend.records import Device
from backend.records.enums import AuditAction, IdentificationLevel, NetworkStatus
from backend.shared_modules.save_device_record import (DeviceChanges, SaveOutcome, SaveStatus,
                                                       UNKNOWN_VENDOR_ID, provisional_level, save_device)
from tests.fakes.fake_device_store import FakeDeviceStore

USER = 7
UNIT = "unit-1"                         # any object works as the unit of work in the fake
FIRST = datetime(2026, 10, 29, 12, 0, 0)
LATER = datetime(2026, 10, 29, 13, 0, 0)
ROUTER_MAC = "70:13:01:65:8E:F7"
PHONE_MAC = "DA:A1:19:00:00:01"         # randomized
OTHER_PHONE_MAC = "DA:A1:19:00:00:02"   # randomized
APPLE_VENDOR = 9


def sighting(mac: str, ip: str = "192.168.1.20", seen_at: datetime = FIRST, **fields: object) -> DeviceChanges:
    """What a scan sends for one host."""
    return DeviceChanges(mac_address=mac, current_ip=ip, seen_at=seen_at, **fields)


def save(devices: FakeDeviceStore, changes: DeviceChanges,
         level: IdentificationLevel = IdentificationLevel.UNIDENTIFIED) -> SaveOutcome:
    return save_device(devices, changes, acting_user_id=USER, level=level, unit=UNIT)


def actions(devices: FakeDeviceStore) -> list[AuditAction]:
    return [entry.action for entry in devices.audit]


class NewDeviceTests(unittest.TestCase):
    def test_new_mac_is_created_seen_online_and_flagged(self) -> None:
        devices = FakeDeviceStore()
        outcome = save(devices, sighting(ROUTER_MAC, "192.168.1.1"))

        self.assertEqual(outcome.status, SaveStatus.CREATED)
        device = outcome.device
        self.assertEqual((device.mac_address, device.current_ip), (ROUTER_MAC, "192.168.1.1"))
        self.assertEqual((device.first_seen, device.last_seen), (FIRST, FIRST))
        self.assertEqual(device.network_status, NetworkStatus.ONLINE)
        self.assertTrue(outcome.flagged)
        self.assertEqual(actions(devices), [AuditAction.INSERT])
        self.assertEqual(devices.audit[0].changed_by, USER)

    def test_new_device_gets_the_unknown_vendor_and_type(self) -> None:     # DI-03
        outcome = save(FakeDeviceStore(), sighting(ROUTER_MAC))
        self.assertEqual((outcome.device.vendor_id, outcome.device.device_type_id), (1, 1))


class SameMacTests(unittest.TestCase):
    """FR-04, AT-02: an IP change updates the same device, never a second one."""

    def test_same_mac_at_a_new_ip_updates_the_same_device(self) -> None:
        devices = FakeDeviceStore()
        first = save(devices, sighting(ROUTER_MAC, "192.168.1.5", FIRST))
        second = save(devices, sighting(ROUTER_MAC, "192.168.1.9", LATER))

        self.assertEqual(second.status, SaveStatus.UPDATED)
        self.assertEqual(second.device.pk, first.device.pk)
        self.assertEqual(len(devices.rows), 1)
        self.assertEqual(second.device.current_ip, "192.168.1.9")
        self.assertEqual((second.device.first_seen, second.device.last_seen), (FIRST, LATER))
        self.assertEqual(actions(devices), [AuditAction.INSERT, AuditAction.UPDATE])

    def test_mac_in_another_format_finds_the_same_device(self) -> None:
        devices = FakeDeviceStore()
        save(devices, sighting(ROUTER_MAC))
        outcome = save(devices, sighting("70-13-01-65-8e-f7", seen_at=LATER))
        self.assertEqual(outcome.status, SaveStatus.UPDATED)
        self.assertEqual(outcome.device.mac_address, ROUTER_MAC)
        self.assertEqual(len(devices.rows), 1)

    def test_fields_left_as_none_keep_what_is_stored(self) -> None:
        devices = FakeDeviceStore([Device(mac_address=ROUTER_MAC, device_name="Living room router",
                                          model="AX3000", hostname="router.lan")])
        outcome = save(devices, sighting(ROUTER_MAC, seen_at=LATER))       # a scan sends no name or model
        self.assertEqual((outcome.device.device_name, outcome.device.model, outcome.device.hostname),
                         ("Living room router", "AX3000", "router.lan"))


class RandomizedMacTests(unittest.TestCase):
    """DI-02, AT-03: randomized MACs are never merged with a different MAC."""

    def test_same_randomized_mac_again_updates_that_device(self) -> None:
        devices = FakeDeviceStore()
        first = save(devices, sighting(PHONE_MAC, is_randomized_mac=True))
        again = save(devices, sighting(PHONE_MAC, "192.168.1.31", LATER, is_randomized_mac=True))
        self.assertEqual(again.status, SaveStatus.UPDATED)
        self.assertEqual(again.device.pk, first.device.pk)
        self.assertEqual(len(devices.rows), 1)

    def test_different_randomized_mac_creates_a_new_device(self) -> None:
        devices = FakeDeviceStore()
        save(devices, sighting(PHONE_MAC, is_randomized_mac=True))
        other = save(devices, sighting(OTHER_PHONE_MAC, is_randomized_mac=True))
        self.assertEqual(other.status, SaveStatus.CREATED)
        self.assertEqual(len(devices.rows), 2)

    def test_randomized_and_fixed_with_the_same_text_stay_separate(self) -> None:
        devices = FakeDeviceStore()
        save(devices, sighting(PHONE_MAC))
        randomized = save(devices, sighting(PHONE_MAC, is_randomized_mac=True))
        self.assertEqual(randomized.status, SaveStatus.CREATED)
        self.assertEqual(len(devices.rows), 2)


class VendorTests(unittest.TestCase):
    """Design 10.4: a scan's OUI match never replaces a vendor that is already known."""

    def test_oui_match_sets_the_vendor_of_a_new_device(self) -> None:
        outcome = save(FakeDeviceStore(), sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(outcome.device.vendor_id, APPLE_VENDOR)

    def test_oui_match_fills_in_an_unknown_vendor(self) -> None:
        devices = FakeDeviceStore([Device(mac_address=ROUTER_MAC, vendor_id=UNKNOWN_VENDOR_ID)])
        outcome = save(devices, sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(outcome.device.vendor_id, APPLE_VENDOR)

    def test_user_set_vendor_is_not_replaced_by_a_scan(self) -> None:
        devices = FakeDeviceStore([Device(mac_address=ROUTER_MAC, vendor_id=42)])     # corrected by a user
        outcome = save(devices, sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR))
        self.assertEqual(outcome.device.vendor_id, 42)

    def test_a_users_vendor_wins_over_an_oui_match_in_the_same_save(self) -> None:
        outcome = save(FakeDeviceStore(), sighting(ROUTER_MAC, oui_vendor_id=APPLE_VENDOR, vendor_id=42))
        self.assertEqual(outcome.device.vendor_id, 42)


class FlagTests(unittest.TestCase):
    """Spec 7.3 through SH-4.1, AT-05."""

    def test_trusted_device_stays_unflagged_after_a_scan(self) -> None:
        devices = FakeDeviceStore([Device(mac_address=ROUTER_MAC, trusted_status=True)])
        outcome = save(devices, sighting(ROUTER_MAC, seen_at=LATER), IdentificationLevel.UNIDENTIFIED)
        self.assertFalse(outcome.flagged)
        self.assertFalse(devices.rows[outcome.device.pk].flagged_status)

    def test_marking_a_device_trusted_clears_its_flag(self) -> None:
        devices = FakeDeviceStore([Device(mac_address=ROUTER_MAC, flagged_status=True)])
        outcome = save(devices, DeviceChanges(mac_address=ROUTER_MAC, trusted_status=True))
        self.assertFalse(outcome.flagged)

    def test_trusted_in_the_same_save_as_a_new_device_is_still_flagged(self) -> None:    # rule 3
        outcome = save(FakeDeviceStore(), sighting(ROUTER_MAC, trusted_status=True))
        self.assertTrue(outcome.device.trusted_status)
        self.assertTrue(outcome.flagged)

    def test_identified_device_is_not_flagged(self) -> None:
        outcome = save(FakeDeviceStore(), sighting(ROUTER_MAC), IdentificationLevel.IDENTIFIED)
        self.assertFalse(outcome.flagged)

    def test_partial_device_is_flagged(self) -> None:
        outcome = save(FakeDeviceStore(), sighting(ROUTER_MAC), IdentificationLevel.PARTIAL)
        self.assertTrue(outcome.flagged)


class RejectionTests(unittest.TestCase):
    """OUT-04: a rejection comes with reasons, and nothing is stored."""

    def assert_rejected(self, changes: DeviceChanges, expected_in_reason: str) -> SaveOutcome:
        devices = FakeDeviceStore()
        outcome = save(devices, changes)
        self.assertEqual(outcome.status, SaveStatus.REJECTED)
        self.assertIsNone(outcome.device)
        self.assertFalse(outcome.flagged)
        self.assertTrue(any(expected_in_reason in reason for reason in outcome.reasons), outcome.reasons)
        self.assertEqual((devices.rows, devices.audit, devices.units_seen), ({}, [], []))   # never called
        return outcome

    def test_invalid_mac(self) -> None:
        self.assert_rejected(sighting("not a mac"), "not a valid MAC address")

    def test_score_of_101(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, confidence_score=101), "Confidence scores go from 0 to 100")

    def test_invalid_ip(self) -> None:
        self.assert_rejected(sighting(ROUTER_MAC, "192.168.1.300"), "Invalid IP address")

    def test_sighting_without_an_ip(self) -> None:
        self.assert_rejected(DeviceChanges(mac_address=ROUTER_MAC, seen_at=FIRST), "needs the IP address")

    def test_every_reason_is_listed(self) -> None:
        outcome = self.assert_rejected(sighting("bad", "bad", confidence_score=-1), "MAC")
        self.assertEqual(len(outcome.reasons), 3)


class UnitOfWorkTests(unittest.TestCase):
    def test_find_and_store_both_use_the_callers_unit(self) -> None:
        devices = FakeDeviceStore()
        save(devices, sighting(ROUTER_MAC))
        self.assertEqual(devices.units_seen, [UNIT, UNIT])
        self.assertEqual(devices.audit[0].unit, UNIT)


class ProvisionalLevelTests(unittest.TestCase):
    def test_known_vendor_is_partial(self) -> None:
        self.assertEqual(provisional_level(APPLE_VENDOR), IdentificationLevel.PARTIAL)

    def test_no_or_unknown_vendor_is_unidentified(self) -> None:
        for vendor_id in (None, UNKNOWN_VENDOR_ID):
            with self.subTest(vendor_id=vendor_id):
                self.assertEqual(provisional_level(vendor_id), IdentificationLevel.UNIDENTIFIED)


if __name__ == "__main__":
    unittest.main()
