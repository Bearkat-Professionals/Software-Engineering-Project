"""Database integration test for Get Device Detail (2.4.2, #120)."""
from __future__ import annotations

import unittest
from datetime import datetime

from backend.data_access.network_data import DeviceStore, ScanStore
from backend.data_access.reference_data import OuiPrefixStore, ReferenceStore
from backend.data_access.repository import Repository
from backend.records import (
    Device,
    DeviceSignature,
    DeviceType,
    IdentificationEvidence,
    Scan,
    ScanObservation,
    User,
    Vendor,
)
from backend.records.enums import EvidenceType
from backend.services.reporting import build_device_detail_service
from backend.services.scan.evidence import weight_for
from tests.data_access.test_repository import connect_to_test_database

CLEANUP = (
    "DELETE FROM alert_history",
    "DELETE FROM identification_evidence",
    "DELETE FROM scan_observation",
    "DELETE FROM device",
    "DELETE FROM oui_prefix",
    "DELETE FROM device_signature",
    "DELETE FROM device_type WHERE device_type_id <> 1",
    "DELETE FROM vendor WHERE vendor_id <> 1",
    "DELETE FROM alert_setting",
    "DELETE FROM audit_log",
    "DELETE FROM scan",
    "DELETE FROM app_user",
)


class DeviceDetailDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = connect_to_test_database()

    def setUp(self) -> None:
        with self.engine.unit_of_work() as unit:
            for sql in CLEANUP:
                unit.execute(sql)

    def test_oui_and_signature_evidence_produce_the_expected_detail(self) -> None:
        user_id = Repository(self.engine, User).create(
            User(username="detail-test", password_hash="not-a-real-hash")
        ).pk
        vendors = ReferenceStore(self.engine, Vendor)
        types = ReferenceStore(self.engine, DeviceType)
        signatures = ReferenceStore(self.engine, DeviceSignature)
        vendor = vendors.store(Vendor(vendor_name="Vantiva"), user_id)
        device_type = types.store(DeviceType(category_name="smart speaker"), user_id)
        signature = signatures.store(DeviceSignature(
            device_type_id=device_type.pk,
            pattern_value="amazon echo",
            evidence_type=EvidenceType.MDNS,
            resulting_device_name="Amazon Echo",
            resulting_model="Dot",
        ), user_id)
        OuiPrefixStore(self.engine).add("70:13:01", vendor.pk, user_id)

        device = DeviceStore(self.engine).store(Device(
            mac_address="70:13:01:65:8E:F7",
            vendor_id=vendor.pk,
            device_type_id=device_type.pk,
            device_name="Amazon Echo",
            model="Dot",
            confidence_score=55,
        ), user_id)
        scans = ScanStore(self.engine)
        started = datetime(2026, 11, 11, 12, 0)
        scan = scans.start(Scan(user_id=user_id, target_scope="192.168.1.0/24", started_at=started))
        scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=device.pk,
                                              ip_address="192.168.1.7", observed_at=started))
        scans.add_evidence(IdentificationEvidence(
            device_id=device.pk, evidence_type=EvidenceType.OUI,
            raw_value="70:13:01", weight=weight_for(EvidenceType.OUI),
        ))
        scans.add_evidence(IdentificationEvidence(
            device_id=device.pk, evidence_type=EvidenceType.MDNS,
            raw_value="amazon echo", signature_id=signature.pk,
            weight=weight_for(EvidenceType.MDNS),
        ))

        service = build_device_detail_service(self.engine)
        detail = service.get_device_detail(device.pk)
        history = service.get_device_history(device.pk)

        self.assertEqual((detail.vendor_name, detail.device_type_name), ("Vantiva", "smart speaker"))
        self.assertEqual(detail.breakdown.score, 55)
        self.assertEqual(len(detail.kept), 2)
        self.assertEqual(detail.dropped, ())
        self.assertFalse(detail.score_outdated)
        self.assertEqual(detail.inferred,
                         frozenset({"vendor", "device_type", "device_name", "model"}))
        self.assertEqual(history.total, 1)
        self.assertEqual(history.entries[0].scan_id, scan.pk)
        self.assertEqual(history.entries[0].started_at, started)


if __name__ == "__main__":
    unittest.main()
