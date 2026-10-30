"""Integration test for 2.2.5.2 Record Observations and Evidence (#41) with the
real SH-4, 3.1 and 3.2 on the TEST database (same one-time setup as
test_repository.py). Skipped without it.

    python -m unittest discover tests
"""
from __future__ import annotations

import unittest
from datetime import datetime

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.network_data import DeviceStore, ScanStore
from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import Repository
from backend.records import Device, DeviceType, OuiPrefix, Scan, User, Vendor
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.match_oui import load_oui_lookup
from backend.services.scan.record_scan_results import record_scan_results
from backend.shared_modules.save_device_record import DeviceRecordSaver
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

FIRST = datetime(2026, 10, 29, 12, 0, 0)     # whole seconds: MySQL DATETIME drops microseconds
LATER = datetime(2026, 10, 29, 13, 0, 0)
HOSTS = [DiscoveredHost("10.0.0.1", "70:13:01:65:8E:F7"),      # router: Vantiva
         DiscoveredHost("10.0.0.20", "00:1B:44:11:3A:B7"),     # no OUI match
         DiscoveredHost("10.0.0.99")]                          # no MAC: skipped


class FailingScanStore(ScanStore):
    """The real store, except observations of the router's IP fail."""
    def add_observation(self, observation, unit=None):
        if observation.ip_address == "10.0.0.1":
            raise RuntimeError("database went away")
        return super().add_observation(observation, unit)


class RecordScanResultsDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = connect_to_test_database()

    def setUp(self) -> None:
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)
        self.user = Repository(self.engine, User).create(
            User(username="scanner", password_hash="not-a-real-hash")).pk
        vendor = Repository(self.engine, Vendor).create(Vendor(vendor_name="Vantiva - Connected Home"))
        Repository(self.engine, OuiPrefix).create(OuiPrefix(oui_prefix="70:13:01", vendor_id=vendor.pk))
        self.vantiva = vendor.pk
        self.saver = DeviceRecordSaver(DeviceStore(self.engine), ReferenceStore(self.engine, Vendor),
                                       ReferenceStore(self.engine, DeviceType))

    def scan(self, seen_at: datetime, scans: ScanStore | None = None):
        scans = scans or ScanStore(self.engine)
        scan = ScanStore(self.engine).start(Scan(user_id=self.user, target_scope="10.0.0.0/24"))
        summary = record_scan_results(scan.pk, self.user, HOSTS, load_oui_lookup(self.engine), seen_at,
                                      engine=self.engine, saver=self.saver, scans=scans)
        return scan.pk, summary

    def count(self, sql: str) -> int:
        return self.engine.fetch_one(f"SELECT COUNT(*) AS n FROM {sql}")["n"]

    def test_two_scans_update_the_same_devices_without_duplicate_evidence(self) -> None:
        first_scan, first = self.scan(FIRST)
        self.assertEqual((first.created, first.updated, first.skipped_no_mac, first.failures), (2, 0, 1, []))
        self.assertEqual(len(first.new_flagged_ids), 2)            # both new, neither fully identified

        second_scan, second = self.scan(LATER)
        self.assertEqual((second.created, second.updated, second.new_flagged_ids), (0, 2, []))

        devices = Repository(self.engine, Device).list()
        self.assertEqual(len(devices), 2)
        self.assertEqual({d.last_seen for d in devices}, {LATER})
        router = DeviceStore(self.engine).find_by_mac("70:13:01:65:8E:F7")
        self.assertEqual(router.vendor_id, self.vantiva)
        self.assertTrue(all(d.flagged_status for d in devices))

        for scan_id in (first_scan, second_scan):
            self.assertEqual(self.count(f"scan_observation WHERE scan_id = {scan_id}"), 2)
        self.assertEqual(self.count("identification_evidence"), 1)          # router's OUI, once
        for device in devices:
            self.assertEqual(len(AuditTrail(self.engine).history(Device.TABLE, device.pk)), 2)

    def test_failed_host_is_rolled_back_and_the_rest_are_saved(self) -> None:
        _, summary = self.scan(FIRST, FailingScanStore(self.engine))
        self.assertEqual([ip for ip, _ in summary.failures], ["10.0.0.1"])
        self.assertEqual(summary.created, 1)
        self.assertIsNone(DeviceStore(self.engine).find_by_mac("70:13:01:65:8E:F7"))   # rolled back
        self.assertEqual(self.count("device"), 1)
        self.assertEqual(self.count("audit_log WHERE record_type = 'device'"), 1)
