"""Unit tests for Get Device Detail (2.4.2, #120). No database required."""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from backend.records import (
    Device,
    DeviceSignature,
    DeviceType,
    IdentificationEvidence,
    OuiPrefix,
    Scan,
    ScanObservation,
    Vendor,
)
from backend.records.enums import EvidenceType, RecordStatus
from backend.services.reporting import DeviceDetailService

NOW = datetime(2026, 11, 11, 12, 0)


class FakeEngine:
    def __init__(self) -> None:
        self.units: list[str] = []

    @contextmanager
    def unit_of_work(self):
        unit = f"unit-{len(self.units) + 1}"
        self.units.append(unit)
        yield unit


class FakeDeviceReader:
    def __init__(self, devices: list[Device]) -> None:
        self.rows = {device.device_id: device for device in devices}
        self.units: list[Any] = []

    def find(self, device_id: int, unit: Any = None) -> Device | None:
        self.units.append(unit)
        return self.rows.get(device_id)


class FakeReferenceReader:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = {row.pk: row for row in rows}
        self.units: list[Any] = []

    def find(self, pk: int, unit: Any = None) -> Any | None:
        self.units.append(unit)
        return self.rows.get(pk)


class FakeOuiPrefixReader:
    def __init__(self, rows: list[OuiPrefix]) -> None:
        self.rows = {row.oui_prefix: row for row in rows}
        self.units: list[Any] = []

    def find(self, prefix: str, unit: Any = None) -> OuiPrefix | None:
        self.units.append(unit)
        return self.rows.get(prefix)


class FakeScanReader:
    def __init__(self, evidence: list[IdentificationEvidence] | None = None,
                 observations: list[ScanObservation] | None = None,
                 scans: list[Scan] | None = None) -> None:
        self.evidence = evidence or []
        self.observations = observations or []
        self.scans = {scan.scan_id: scan for scan in (scans or [])}
        self.units: list[Any] = []

    def evidence_for(self, device_id: int, unit: Any = None) -> list[IdentificationEvidence]:
        self.units.append(unit)
        return [row for row in self.evidence if row.device_id == device_id]

    def device_history(self, device_id: int, start=None, end=None,
                       unit: Any = None) -> list[ScanObservation]:
        self.units.append(unit)
        return [row for row in self.observations if row.device_id == device_id]

    def find(self, scan_id: int, unit: Any = None) -> Scan | None:
        self.units.append(unit)
        return self.scans.get(scan_id)


def evidence(evidence_id: int, kind: EvidenceType, raw: str, weight: int,
             signature_id: int | None = None) -> IdentificationEvidence:
    return IdentificationEvidence(evidence_id=evidence_id, device_id=7, evidence_type=kind,
                                  raw_value=raw, signature_id=signature_id, weight=weight)


def signature(signature_id: int, kind: EvidenceType, device_type_id: int,
              *, name: str | None = None, model: str | None = None,
              status: RecordStatus = RecordStatus.ACTIVE) -> DeviceSignature:
    return DeviceSignature(signature_id=signature_id, device_type_id=device_type_id,
                           pattern_value=f"pattern-{signature_id}", evidence_type=kind,
                           resulting_device_name=name, resulting_model=model, status=status)


class _ServiceFixture:
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.device = Device(device_id=7, mac_address="70:13:01:65:8E:F7",
                             vendor_id=2, device_type_id=4)
        self.devices = FakeDeviceReader([self.device])
        self.scans = FakeScanReader()
        self.vendors = FakeReferenceReader([
            Vendor(vendor_id=1, vendor_name="unknown"),
            Vendor(vendor_id=2, vendor_name="Vantiva"),
        ])
        self.types = FakeReferenceReader([
            DeviceType(device_type_id=1, category_name="unknown"),
            DeviceType(device_type_id=4, category_name="smart speaker"),
            DeviceType(device_type_id=5, category_name="television"),
        ])
        self.signatures = FakeReferenceReader([])
        self.prefixes = FakeOuiPrefixReader([OuiPrefix(oui_id=1, oui_prefix="70:13:01", vendor_id=2)])

    def service(self) -> DeviceDetailService:
        return DeviceDetailService(self.engine, self.devices, self.scans, self.vendors,
                                   self.types, self.signatures, self.prefixes)


class DeviceDetailTests(_ServiceFixture, unittest.TestCase):

    def test_no_evidence_has_an_empty_breakdown_and_nothing_inferred(self) -> None:
        detail = self.service().get_device_detail(7)

        self.assertEqual((detail.vendor_name, detail.device_type_name), ("Vantiva", "smart speaker"))
        self.assertEqual((detail.kept, detail.dropped), ((), ()))
        self.assertEqual(detail.breakdown.score, 0)
        self.assertFalse(detail.score_outdated)
        self.assertEqual(detail.inferred, frozenset())

    def test_oui_only_marks_the_vendor_inferred(self) -> None:
        self.device.confidence_score = 20
        self.scans.evidence = [evidence(1, EvidenceType.OUI, "70:13:01", 20)]

        detail = self.service().get_device_detail(7)

        self.assertEqual(detail.breakdown.score, 20)
        self.assertEqual(detail.inferred, frozenset({"vendor"}))
        self.assertEqual([row.evidence.evidence_id for row in detail.kept], [1])

    def test_stronger_type_is_counted_and_weaker_type_is_conflicting(self) -> None:
        self.device.confidence_score = 55
        self.device.device_name = "Amazon Echo"
        self.device.model = "Dot"
        strong = signature(11, EvidenceType.MDNS, 4, name="Amazon Echo", model="Dot")
        weak = signature(12, EvidenceType.REVERSE_DNS, 5, name="Living Room TV")
        self.signatures = FakeReferenceReader([strong, weak])
        self.scans.evidence = [
            evidence(1, EvidenceType.OUI, "70:13:01", 20),
            evidence(2, EvidenceType.MDNS, "_amazon._tcp", 35, strong.pk),
            evidence(3, EvidenceType.REVERSE_DNS, "living-tv", 20, weak.pk),
        ]

        detail = self.service().get_device_detail(7)

        self.assertEqual(detail.breakdown.score, 55)
        self.assertEqual({row.evidence.evidence_id for row in detail.kept}, {1, 2})
        self.assertEqual([row.evidence.evidence_id for row in detail.dropped], [3])
        self.assertEqual(detail.dropped[0].resulting_device_name, "Living Room TV")
        self.assertEqual(detail.inferred, frozenset({"vendor", "device_type", "device_name", "model"}))

    def test_a_user_edited_name_is_not_inferred(self) -> None:
        self.device.device_name = "Kitchen speaker"
        match = signature(11, EvidenceType.MDNS, 4, name="Amazon Echo", model="Dot")
        self.signatures = FakeReferenceReader([match])
        self.scans.evidence = [evidence(1, EvidenceType.MDNS, "_amazon._tcp", 35, match.pk)]

        detail = self.service().get_device_detail(7)

        self.assertNotIn("device_name", detail.inferred)
        self.assertIn("device_type", detail.inferred)

    def test_a_retired_signature_still_shows_its_historical_result(self) -> None:
        retired = signature(11, EvidenceType.MDNS, 4, name="Amazon Echo", model="Dot",
                            status=RecordStatus.RETIRED)
        self.signatures = FakeReferenceReader([retired])
        self.scans.evidence = [evidence(1, EvidenceType.MDNS, "_amazon._tcp", 35, retired.pk)]

        detail = self.service().get_device_detail(7)

        self.assertEqual(detail.kept[0].resulting_device_name, "Amazon Echo")
        self.assertEqual(detail.kept[0].resulting_model, "Dot")
        self.assertEqual(detail.breakdown.score, 35)

    def test_a_different_stored_and_recalculated_score_is_outdated(self) -> None:
        self.device.confidence_score = 80
        self.scans.evidence = [evidence(1, EvidenceType.OUI, "70:13:01", 20)]

        detail = self.service().get_device_detail(7)

        self.assertEqual(detail.breakdown.score, 20)
        self.assertTrue(detail.score_outdated)

    def test_an_unknown_device_returns_none(self) -> None:
        self.assertIsNone(self.service().get_device_detail(999))

    def test_every_detail_read_uses_one_unit_of_work(self) -> None:
        match = signature(11, EvidenceType.MDNS, 4)
        self.signatures = FakeReferenceReader([match])
        self.scans.evidence = [
            evidence(1, EvidenceType.OUI, "70:13:01", 20),
            evidence(2, EvidenceType.MDNS, "speaker", 35, match.pk),
        ]

        self.service().get_device_detail(7)

        self.assertEqual(self.engine.units, ["unit-1"])
        used = (self.devices.units + self.scans.units + self.vendors.units + self.types.units
                + self.signatures.units + self.prefixes.units)
        self.assertTrue(used)
        self.assertEqual(set(used), {"unit-1"})


class DeviceHistoryTests(_ServiceFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        scans = [Scan(scan_id=i, user_id=1, target_scope="192.168.1.0/24",
                      started_at=NOW - timedelta(days=i)) for i in range(1, 4)]
        observations = [ScanObservation(observation_id=i, scan_id=i, device_id=7,
                                        observed_at=scan.started_at)
                        for i, scan in enumerate(scans, start=1)]
        self.scans = FakeScanReader(observations=observations, scans=scans)

    def test_history_is_paged_and_includes_scan_start_time(self) -> None:
        page = self.service().get_device_history(7, page=2, per_page=2)

        self.assertEqual((page.page, page.per_page, page.total), (2, 2, 3))
        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].scan_id, 3)
        self.assertEqual(page.entries[0].started_at, NOW - timedelta(days=3))
        self.assertEqual(self.engine.units, ["unit-1"])
        self.assertEqual(set(self.scans.units), {"unit-1"})

    def test_unknown_device_history_returns_none_without_reading_scans(self) -> None:
        self.assertIsNone(self.service().get_device_history(999))
        self.assertEqual(self.engine.units, ["unit-1"])
        self.assertEqual(self.devices.units, ["unit-1"])
        self.assertEqual(self.scans.units, [])

    def test_history_rejects_bad_page_values(self) -> None:
        for page, per_page in ((0, 50), (1, 0), (1, 201)):
            with self.subTest(page=page, per_page=per_page):
                with self.assertRaises(ValueError):
                    self.service().get_device_history(7, page, per_page)


if __name__ == "__main__":
    unittest.main()
