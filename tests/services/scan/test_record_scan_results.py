"""Unit tests for 2.2.5.2 Record Observations and Evidence (#41). No database:
SH-4 runs for real on the fake device and reference stores.

    python -m unittest discover tests
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from backend.records import Device, DeviceType, IdentificationEvidence, OuiPrefix, ScanObservation, Vendor
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.match_oui import build_oui_lookup
from backend.services.scan.record_scan_results import record_scan_results
from backend.shared_modules.save_device_record import DeviceRecordSaver
from tests.fakes.fake_device_store import FakeDeviceStore
from tests.fakes.fake_reference_store import FakeReferenceStore

FIRST = datetime(2026, 10, 29, 12, 0, 0)
LATER = datetime(2026, 10, 29, 13, 0, 0)
ROUTER = DiscoveredHost("10.0.0.1", "70:13:01:65:8E:F7")       # OUI 70:13:01 -> Vantiva
LAPTOP = DiscoveredHost("10.0.0.20", "00:1B:44:11:3A:B7")      # OUI not in the lookup
VANTIVA = Vendor(vendor_id=2, vendor_name="Vantiva - Connected Home")
LOOKUP = build_oui_lookup([VANTIVA], [OuiPrefix(oui_prefix="70:13:01", vendor_id=2)])


class FakeEngine:
    """unit_of_work() hands out "unit-1", "unit-2", ... so tests can see one unit per host."""
    def __init__(self) -> None:
        self.units: list[str] = []

    @contextmanager
    def unit_of_work(self):
        self.units.append(f"unit-{len(self.units) + 1}")
        yield self.units[-1]


class FakeScanStore:
    """3.1.4's add_observation and add_evidence, in memory, with the real store's rules:
    one observation per (scan, device), and evidence the device already has is not added again."""
    def __init__(self, fail_for_device: int | None = None) -> None:
        self.observations: dict[tuple[int, int], ScanObservation] = {}
        self.evidence: list[IdentificationEvidence] = []
        self.fail_for_device = fail_for_device

    def add_observation(self, observation: ScanObservation, unit: Any = None) -> ScanObservation:
        if observation.device_id == self.fail_for_device:
            raise RuntimeError("database went away")
        self.observations[(observation.scan_id, observation.device_id)] = observation
        return observation

    def add_evidence(self, evidence: IdentificationEvidence, unit: Any = None):
        key = (evidence.device_id, evidence.evidence_type, evidence.raw_value, evidence.signature_id)
        for existing in self.evidence:
            if (existing.device_id, existing.evidence_type, existing.raw_value, existing.signature_id) == key:
                return existing, False
        self.evidence.append(evidence)
        return evidence, True


class RecordScanResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = FakeDeviceStore()
        self.scans = FakeScanStore()
        self.engine = FakeEngine()

    def record(self, hosts, scan_id: int = 1, seen_at: datetime = FIRST):
        saver = DeviceRecordSaver(self.devices, FakeReferenceStore(Vendor, [VANTIVA]),
                                  FakeReferenceStore(DeviceType))
        return record_scan_results(scan_id, 1, hosts, LOOKUP, seen_at,
                                   engine=self.engine, saver=saver, scans=self.scans)

    def test_new_and_existing_hosts(self) -> None:
        self.devices = FakeDeviceStore([Device(mac_address=LAPTOP.mac_address)])    # seen before
        summary = self.record([ROUTER, LAPTOP])

        self.assertEqual((summary.created, summary.updated, summary.failures), (1, 1, []))
        router = self.devices.find_by_mac(ROUTER.mac_address)
        self.assertEqual(router.vendor_id, 2)                       # the OUI vendor
        self.assertEqual(router.last_seen, FIRST)
        self.assertEqual(summary.new_flagged_ids, [router.pk])      # only NEW flagged devices
        self.assertEqual(len(self.scans.observations), 2)
        self.assertEqual([e.raw_value for e in self.scans.evidence], ["70:13:01"])   # laptop has no OUI match
        self.assertEqual(self.engine.units, ["unit-1", "unit-2"])   # one unit of work per host
        self.assertEqual({entry.unit for entry in self.devices.audit}, {"unit-1", "unit-2"})

    def test_host_without_mac_is_skipped(self) -> None:
        summary = self.record([DiscoveredHost("10.0.0.5"), ROUTER])
        self.assertEqual((summary.skipped_no_mac, summary.created), (1, 1))
        self.assertEqual(len(self.devices.rows), 1)

    def test_two_hosts_with_one_mac_are_recorded_once(self) -> None:
        second_ip = DiscoveredHost("10.0.0.2", "70-13-01-65-8e-f7")     # same MAC, other spelling
        summary = self.record([ROUTER, second_ip])
        self.assertEqual((summary.created, summary.duplicate_macs), (1, 1))
        [observation] = self.scans.observations.values()
        self.assertEqual(observation.ip_address, "10.0.0.1")           # the first one is kept

    def test_rejected_host_does_not_stop_the_rest(self) -> None:
        bad_ip = DiscoveredHost("999.0.0.1", "00:1B:44:11:3A:B8")
        summary = self.record([bad_ip, ROUTER])
        self.assertEqual(summary.created, 1)
        [(ip, reason)] = summary.failures
        self.assertEqual(ip, "999.0.0.1")
        self.assertTrue(reason)

    def test_database_error_for_one_host_does_not_stop_the_rest(self) -> None:
        self.scans = FakeScanStore(fail_for_device=1)       # the first device saved gets id 1
        summary = self.record([ROUTER, LAPTOP])
        self.assertEqual(summary.failures, [("10.0.0.1", "RuntimeError: database went away")])
        self.assertEqual(summary.created, 1)                # the laptop still got saved
        self.assertEqual(len(self.scans.observations), 1)

    def test_rescan_updates_and_does_not_duplicate_evidence(self) -> None:
        self.record([ROUTER, LAPTOP], scan_id=1, seen_at=FIRST)
        second = self.record([ROUTER, LAPTOP], scan_id=2, seen_at=LATER)

        self.assertEqual((second.created, second.updated, second.new_flagged_ids), (0, 2, []))
        self.assertEqual(len(self.devices.rows), 2)
        self.assertEqual(self.devices.find_by_mac(ROUTER.mac_address).last_seen, LATER)
        self.assertEqual(sorted(self.scans.observations), [(1, 1), (1, 2), (2, 1), (2, 2)])
        self.assertEqual(len(self.scans.evidence), 1)
