"""Integration tests for 2.2.5.2 Record Observations and Evidence (#41) with identification
(2.2.4), the real SH-4, 3.1 and 3.2 on the TEST database (same one-time setup as
test_repository.py). Skipped without it.

    python -m unittest tests.services.scan.test_record_scan_results_db -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.network_data import DeviceStore, ScanStore
from backend.data_access.reference_data import ReferenceStore, find_reference_rules
from backend.data_access.repository import Repository
from backend.records import (Device, DeviceSignature, DeviceType, IdentificationEvidence, OuiPrefix, Scan,
                             User, Vendor)
from backend.records.enums import EvidenceType
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.evidence import UNKNOWN_TYPE_ID
from backend.services.scan.identification_manager import IdentificationRules
from backend.services.scan.record_scan_results import RecordSummary, record_scan_results
from backend.shared_modules.save_device_record import DeviceRecordSaver
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

# CLEANUP keeps device types and signatures; these tests add them, so clear them too
EXTRA_CLEANUP = ("DELETE FROM device_signature", "DELETE FROM device_type WHERE device_type_id <> 1")

FIRST = datetime(2026, 10, 29, 12, 0, 0)     # whole seconds: MySQL DATETIME drops microseconds
LATER = datetime(2026, 10, 29, 13, 0, 0)
LAST = datetime(2026, 10, 29, 14, 0, 0)
ROUTER_MAC = "70:13:01:65:8E:F7"
HOSTS = [DiscoveredHost("10.0.0.1", ROUTER_MAC),               # router: Vantiva
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
            for sql in CLEANUP + EXTRA_CLEANUP:
                cursor.execute(sql)
        self.user = Repository(self.engine, User).create(
            User(username="scanner", password_hash="not-a-real-hash")).pk
        vendor = Repository(self.engine, Vendor).create(Vendor(vendor_name="Vantiva - Connected Home"))
        Repository(self.engine, OuiPrefix).create(OuiPrefix(oui_prefix="70:13:01", vendor_id=vendor.pk))
        self.vantiva = vendor.pk
        self.saver = DeviceRecordSaver(DeviceStore(self.engine), ReferenceStore(self.engine, Vendor),
                                       ReferenceStore(self.engine, DeviceType))

    # ---- helpers -----------------------------------------------------------------------

    def scan(self, seen_at: datetime, scans: ScanStore | None = None) -> tuple[int, RecordSummary]:
        """One scan of HOSTS, with the rules loaded fresh, as the Scan Manager does."""
        scans = scans or ScanStore(self.engine)
        scan = ScanStore(self.engine).start(Scan(user_id=self.user, target_scope="10.0.0.0/24"))
        rules = IdentificationRules.from_reference(find_reference_rules(self.engine))
        summary = record_scan_results(scan.pk, self.user, HOSTS, rules, seen_at, engine=self.engine,
                                      saver=self.saver, devices=DeviceStore(self.engine), scans=scans)
        return scan.pk, summary

    def count(self, sql: str) -> int:
        return self.engine.fetch_one(f"SELECT COUNT(*) AS n FROM {sql}")["n"]

    def router(self) -> Device:
        return DeviceStore(self.engine).find_by_mac(ROUTER_MAC)

    def echo_signature(self) -> DeviceSignature:
        speaker = Repository(self.engine, DeviceType).create(DeviceType(category_name="smart speaker"))
        return Repository(self.engine, DeviceSignature).create(DeviceSignature(
            device_type_id=speaker.pk, pattern_value="echo", evidence_type=EvidenceType.MDNS,
            resulting_device_name="Echo Dot", resulting_model="3rd gen"))

    def heard_echo(self, device_id: int, signature: DeviceSignature) -> None:
        """Stands in for an M6 collector: the device answered mDNS as "kitchen-echo"."""
        ScanStore(self.engine).add_evidence(IdentificationEvidence(
            device_id=device_id, evidence_type=EvidenceType.MDNS, raw_value="kitchen-echo",
            signature_id=signature.pk, weight=35))

    def snapshot(self) -> dict[int, tuple[Any, ...]]:
        """What identification decides for each device."""
        return {d.pk: (d.confidence_score, d.device_type_id, d.vendor_id, d.flagged_status, d.device_name, d.model)
                for d in Repository(self.engine, Device).list()}

    # ---- saving ------------------------------------------------------------------------

    def test_two_scans_update_the_same_devices_without_duplicate_evidence(self) -> None:
        first_scan, first = self.scan(FIRST)
        self.assertEqual((first.created, first.updated, first.skipped_no_mac, first.failures), (2, 0, 1, []))
        self.assertEqual(len(first.new_flagged_ids), 2)            # both new, neither fully identified

        second_scan, second = self.scan(LATER)
        self.assertEqual((second.created, second.updated, second.new_flagged_ids), (0, 2, []))

        devices = Repository(self.engine, Device).list()
        self.assertEqual(len(devices), 2)
        self.assertEqual({d.last_seen for d in devices}, {LATER})
        self.assertEqual(self.router().vendor_id, self.vantiva)
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
        self.assertIsNone(self.router())                                   # rolled back
        self.assertEqual(self.count("device"), 1)
        self.assertEqual(self.count("audit_log WHERE record_type = 'device'"), 1)

    # ---- identification ----------------------------------------------------------------

    def test_rescanning_changes_no_score_type_or_flag_and_adds_no_evidence(self) -> None:
        signature = self.echo_signature()
        self.scan(FIRST)
        self.heard_echo(self.router().pk, signature)
        self.scan(LATER)                                                   # identified from stored evidence
        router = self.router()
        self.assertEqual((router.confidence_score, router.device_type_id, router.flagged_status),
                         (55, signature.device_type_id, False))            # OUI 20 + mDNS 35
        before, evidence_before = self.snapshot(), self.count("identification_evidence")

        self.scan(LAST)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.count("identification_evidence"), evidence_before)

    def test_a_retired_signature_stops_counting_but_the_type_is_kept(self) -> None:
        signature = self.echo_signature()
        self.scan(FIRST)
        self.heard_echo(self.router().pk, signature)
        self.scan(LATER)
        self.assertEqual(self.router().device_type_id, signature.device_type_id)

        ReferenceStore(self.engine, DeviceSignature).retire(signature.pk, self.user)
        self.scan(LAST)
        router = self.router()
        self.assertEqual((router.confidence_score, router.flagged_status), (20, True))   # OUI only again
        # scans never clear a type or name (#104): a user changes them
        self.assertEqual((router.device_type_id, router.device_name), (signature.device_type_id, "Echo Dot"))
        self.assertEqual(self.count(f"identification_evidence WHERE signature_id = {signature.pk}"), 1)  # kept

    def test_a_users_name_and_type_survive_a_rescan(self) -> None:
        signature = self.echo_signature()
        printer = Repository(self.engine, DeviceType).create(DeviceType(category_name="printer"))
        self.scan(FIRST)
        router = self.router()
        router.device_name = "Front hall printer"                          # as the edit screen will
        router.device_type_id = printer.pk
        DeviceStore(self.engine).store(router, self.user)
        self.heard_echo(router.pk, signature)                              # evidence says "speaker"

        self.scan(LATER)
        router = self.router()
        self.assertEqual((router.device_name, router.device_type_id), ("Front hall printer", printer.pk))
        self.assertEqual(router.model, "3rd gen")                          # the empty model is filled in


if __name__ == "__main__":
    unittest.main()