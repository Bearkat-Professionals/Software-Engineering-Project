import json
import unittest
from datetime import datetime, timedelta, timezone
from backend.data_access.network_data import DeviceListing, DeviceSearch
from backend.records import Device, DeviceSignature, DeviceType, IdentificationEvidence, OuiPrefix, Scan, ScanObservation, User, Vendor
from backend.records.enums import EvidenceType, ScanOutcome, ScanStatus
from backend.services.records.reference_maintenance import MaintenanceStatus
from backend.services.reporting.device_detail import DeviceDetailService
from backend.services.reporting.export_json import MAX_EXPORT_DEVICES, ExportService, device_export, export_text, iso_utc, scan_export
from tests.services.reporting.test_device_detail import FakeDeviceReader, FakeEngine, FakeOuiPrefixReader, FakeReferenceReader, FakeScanReader

HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
SECRET_KEY = "Secret-key-that-might-be-related-to-Javier"
JAVIER = User(user_id=3, username="javier", password_hash=HASH)
MARK = User(user_id=4, username="mark", password_hash=HASH)

STRONG = DeviceSignature(signature_id=11, device_type_id=4, pattern_value="_amazon._tcp", evidence_type=EvidenceType.MDNS, resulting_device_name="Amazon Echo")
WEAK = DeviceSignature(signature_id=12, device_type_id=5, pattern_value="living-tv", evidence_type=EvidenceType.REVERSE_DNS, resulting_device_name="Living Room TV")
TIED = DeviceSignature(signature_id=13, device_type_id=5, pattern_value="roku", evidence_type=EvidenceType.SSDP, resulting_device_name="Roku")
SIGNATURES = {11: STRONG, 12: WEAK, 13: TIED}

OUI = IdentificationEvidence(evidence_id=1, device_id=7, evidence_type=EvidenceType.OUI, raw_value="70:13:01", weight=20)
MDNS = IdentificationEvidence(evidence_id=2, device_id=7, evidence_type=EvidenceType.MDNS, raw_value="_amazon._tcp", weight=35, signature_id=11)
REVERSE_DNS = IdentificationEvidence(evidence_id=3, device_id=7, evidence_type=EvidenceType.REVERSE_DNS, raw_value="living-tv", weight=20, signature_id=12)
SSDP = IdentificationEvidence(evidence_id=3, device_id=7, evidence_type=EvidenceType.SSDP, raw_value="roku", weight=35, signature_id=13)
CONFLICTING = [OUI, MDNS, REVERSE_DNS]
TIE = [OUI, MDNS, SSDP]

ECHO = DeviceListing(Device(device_id=7, mac_address="70:13:01:65:8E:F7", device_type_id=4, vendor_id=2, device_name="Amazon Echo", confidence_score=55), "Vantiva", "smart speaker")
PHONE = DeviceListing(Device(device_id=8, mac_address="DA:A1:19:00:00:01", is_randomized_mac=True), "unknown", "unknown")
SCAN = Scan(scan_id=9, user_id=4, target_scope="192.168.1.0/24", status=ScanStatus.COMPLETED, outcome=ScanOutcome.SUCCESS, started_at=datetime(2026, 11, 16, 8, 0), configuration_used={"secret_key": SECRET_KEY, "sid": "k3J9", "password": "letmein"})
OBSERVATIONS = [ScanObservation(observation_id=21, scan_id=9, device_id=7, ip_address="192.168.1.20"), ScanObservation(observation_id=22, scan_id=9, device_id=8)]


class FakeDevices:
    def __init__(self, listings):
        self.listings = listings

    def search_all(self, search, limit, unit=None):
        return self.listings[:limit + 1]

    def listings_for(self, device_ids, unit=None):
        found = []
        for listing in self.listings:
            if listing.device.device_id in device_ids:
                found.append(listing)
        return found


class FakeScans:
    def __init__(self, evidence, observations):
        self.evidence = evidence
        self.observations = observations

    def find(self, scan_id, unit=None):
        if scan_id == 9:
            return SCAN
        return None

    def observations_for(self, scan_id, unit=None):
        return self.observations

    def evidence_for(self, device_id, unit=None):
        found = []
        for record in self.evidence:
            if record.device_id == device_id:
                found.append(record)
        return found


class FakeRecords:
    def __init__(self, records):
        self.records = {}
        for record in records:
            self.records[record.pk] = record

    def find(self, pk, unit=None):
        return self.records.get(pk)


def make_service(listings=None, evidence=None, observations=None):
    if listings is None:
        listings = [ECHO, PHONE]
    if evidence is None:
        evidence = CONFLICTING
    if observations is None:
        observations = OBSERVATIONS
    return ExportService(FakeEngine().unit_of_work, FakeDevices(listings), FakeScans(evidence, observations), FakeRecords([STRONG, WEAK, TIED]), FakeRecords([JAVIER, MARK]))


class TestExportJson(unittest.TestCase):

    def test_device_keys_in_order(self):
        part = device_export(ECHO, CONFLICTING, SIGNATURES)
        self.assertEqual(list(part), ["device_id", "observed", "identification", "user_marks", "evidence"])
        self.assertEqual(list(part["observed"]), ["mac", "is_randomized_mac", "ip", "hostname", "network_status", "first_seen", "last_seen"])
        self.assertEqual(list(part["identification"]), ["type", "vendor", "name", "model", "level", "confidence_score"])
        self.assertEqual(list(part["user_marks"]), ["trusted", "flagged", "flag_set_by_user"])
        self.assertEqual(list(part["evidence"][0]), ["evidence_type", "raw_value", "weight", "signature_id", "counted"])

    def test_scan_and_document_keys_in_order(self):
        part = scan_export(SCAN, "mark", OBSERVATIONS)
        self.assertEqual(list(part), ["scan_id", "scope", "started_by", "started_at", "ended_at", "outcome", "errors", "observations"])
        self.assertEqual(list(part["observations"][0]), ["device_id", "ip", "hostname", "status", "observed_at"])

        document = make_service().export_devices(DeviceSearch(), JAVIER).document
        self.assertEqual(list(document), ["format", "format_version", "exported_at", "exported_by", "scan", "device_count", "devices"])

    def test_every_key_present_with_null_when_empty(self):
        part = device_export(PHONE, [], {})
        self.assertEqual(part["observed"]["ip"], None)
        self.assertEqual(part["observed"]["first_seen"], None)
        self.assertEqual(part["identification"]["name"], None)
        self.assertEqual(part["evidence"], [])

        scan = scan_export(SCAN, None, [])
        self.assertEqual(scan["started_by"], None)
        self.assertEqual(scan["ended_at"], None)
        self.assertEqual(scan["errors"], None)

    def test_counted_matches_device_detail(self):
        for records in [CONFLICTING, TIE]:
            detail = DeviceDetailService(FakeEngine(), FakeDeviceReader([ECHO.device]), FakeScanReader(evidence=records), FakeReferenceReader([Vendor(vendor_id=2, vendor_name="Vantiva")]), FakeReferenceReader([DeviceType(device_type_id=4, category_name="smart speaker")]), FakeReferenceReader([STRONG, WEAK, TIED]), FakeOuiPrefixReader([OuiPrefix(oui_id=1, oui_prefix="70:13:01", vendor_id=2)])).get_device_detail(7)
            kept = []
            for row in detail.kept:
                kept.append(row.evidence.raw_value)

            part = device_export(ECHO, records, SIGNATURES)
            counted = []
            for row in part["evidence"]:
                if row["counted"]:
                    counted.append(row["raw_value"])

            self.assertEqual(sorted(counted), sorted(kept))

    def test_service_looks_up_the_signatures(self):
        document = make_service().export_devices(DeviceSearch(), JAVIER).document
        counted = []
        for row in document["devices"][0]["evidence"]:
            if row["counted"]:
                counted.append(row["raw_value"])
        self.assertEqual(counted, ["70:13:01", "_amazon._tcp"])

    def test_utc_near_midnight(self):
        central = timezone(timedelta(hours=-6))
        self.assertEqual(iso_utc(datetime(2026, 11, 16, 23, 30, tzinfo=central)), "2026-11-17T05:30:00Z")
        self.assertEqual(iso_utc(datetime(2026, 11, 16, 20, 5, 0, 999, tzinfo=timezone.utc)), "2026-11-16T20:05:00Z")
        self.assertEqual(iso_utc(None), None)

    def test_over_the_limit_is_rejected(self):
        listings = []
        for i in range(MAX_EXPORT_DEVICES + 1):
            listings.append(PHONE)
        result = make_service(listings=listings).export_devices(DeviceSearch(), JAVIER)
        self.assertEqual(result.status, MaintenanceStatus.REJECTED)
        self.assertIn("Narrow the filters", result.reason)

    def test_unknown_scan_is_not_found(self):
        result = make_service().export_scan(404, JAVIER)
        self.assertEqual(result.status, MaintenanceStatus.NOT_FOUND)

    def test_at_10_no_secrets_in_the_text(self):
        results = [make_service().export_devices(DeviceSearch(), JAVIER), make_service().export_scan(9, JAVIER)]
        for result in results:
            text = export_text(result.document)
            json.loads(text)
            for secret in ["password", "hash", "$argon2", "sid", SECRET_KEY]:
                self.assertNotIn(secret, text.lower())

    def test_same_bytes_every_run(self):
        first = make_service().export_scan(9, JAVIER).document

        evidence = list(CONFLICTING)
        evidence.reverse()
        observations = list(OBSERVATIONS)
        observations.reverse()
        second = make_service(evidence=evidence, observations=observations).export_scan(9, JAVIER).document

        first["exported_at"] = None
        second["exported_at"] = None
        self.assertEqual(export_text(first), export_text(second))


if __name__ == "__main__":
    unittest.main()