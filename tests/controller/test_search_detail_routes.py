"""Tests for the search and detail API (#121): GET /api/devices with search parameters,
GET /api/devices/<id> with its detail, and GET /api/devices/<id>/history. Flask's test
client with fake services: no database or network.

    python -m unittest tests.controller.test_search_detail_routes -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from flask.testing import FlaskClient

from backend.controller.app_controller import Services, create_app
from backend.data_access.network_data import DeviceListing, DevicePage, DeviceSearch
from backend.records import Device, IdentificationEvidence, ScanObservation, User
from backend.records.enums import EvidenceType, NetworkStatus, UserRole
from backend.services.reporting import DeviceDetail, DeviceHistoryEntry, DeviceHistoryPage, EvidenceDetail
from backend.services.scan.calculate_score import Contribution, ScoreBreakdown
from backend.services.scan.evidence import EvidenceItem
from backend.services.records.device_maintenance import LoadedDevice
from backend.services.scan.scan_session import ScanState, ScanStatusReport
from tests.controller.test_api_routes import FakeScanManager, FakeScans, FakeSessions
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers

VIEWER = User(user_id=5, username="val", password_hash="not-a-real-hash", role=UserRole.VIEWER)
MISSING = {"error": "There is no device with id 99. It may have been deleted; reload the list."}
DEVICE = Device(device_id=7, mac_address="F0:D1:A9:12:34:56", current_ip="192.168.1.23", device_type_id=4,
                vendor_id=12, device_name="Office printer", confidence_score=20, network_status=NetworkStatus.ONLINE)

# Device 7's evidence: an OUI and an mDNS signature that count, and an SSDP one that lost a conflict
OUI = IdentificationEvidence(evidence_id=1, device_id=7, evidence_type=EvidenceType.OUI, raw_value="F0:D1:A9",
                             weight=20)
MDNS = IdentificationEvidence(evidence_id=2, device_id=7, evidence_type=EvidenceType.MDNS,
                              raw_value="Office-Printer._ipp._tcp", signature_id=31, weight=35)
SSDP = IdentificationEvidence(evidence_id=3, device_id=7, evidence_type=EvidenceType.SSDP,
                              raw_value="urn:schemas-upnp-org:device:MediaRenderer:1", signature_id=40, weight=35)
DETAIL = DeviceDetail(
    device=DEVICE, vendor_name="HP Inc.", device_type_name="printer",
    kept=(EvidenceDetail(OUI), EvidenceDetail(MDNS, "Office printer", "LaserJet 4000")),
    dropped=(EvidenceDetail(SSDP, "Living room TV", None),),
    breakdown=ScoreBreakdown(55, (Contribution(EvidenceType.MDNS, 35, (EvidenceItem.from_record(MDNS),)),
                                  Contribution(EvidenceType.OUI, 20, (EvidenceItem.from_record(OUI),))), False),
    score_outdated=True, inferred=frozenset({"vendor", "device_type"}))
DETAIL_JSON = {
    "inferred": ["device_type", "vendor"],
    "score": {"stored": 20, "calculated": 55, "capped": False, "outdated": True, "contributions": [
        {"evidence_type": "mdns", "points": 35, "evidence": [
            {"raw_value": "Office-Printer._ipp._tcp",
             "signature": {"signature_id": 31, "device_name": "Office printer", "model": "LaserJet 4000"},
             "weight": 35}]},
        {"evidence_type": "oui", "points": 20, "evidence": [
            {"raw_value": "F0:D1:A9", "signature": None, "weight": 20}]}]},
    "conflicting": [
        {"evidence_type": "ssdp", "raw_value": "urn:schemas-upnp-org:device:MediaRenderer:1",
         "signature": {"signature_id": 40, "device_name": "Living room TV", "model": None}, "weight": 35}],
}

STARTED = datetime(2026, 11, 8, 9, 0, 0)
HISTORY = [DeviceHistoryEntry(ScanObservation(scan_id=scan_id, device_id=7, ip_address=f"192.168.1.{scan_id}",
                                              status=NetworkStatus.ONLINE,
                                              observed_at=datetime(2026, 11, 8, 9, scan_id, 5)),
                              scan_id, datetime(2026, 11, 8, 9, scan_id, 0))
           for scan_id in (12, 11, 10)]                       # newest first, as the service gives them


class FakeLoad:
    """DeviceMaintenance.load for device 7 only (the edit form's half of GET /api/devices/<id>)."""
    def load(self, device_id: int) -> LoadedDevice:
        if device_id != 7:
            return LoadedDevice(None, None)
        return LoadedDevice(DEVICE, datetime(2026, 11, 8, 10, 15, 2), "HP Inc.", "printer")


class FakeSearch:
    """DeviceStore.search: remembers each DeviceSearch and answers with one device."""
    def __init__(self) -> None:
        self.calls: list[tuple[DeviceSearch, int, int]] = []

    def search(self, search: DeviceSearch = DeviceSearch(), page: int = 1, per_page: int = 50) -> DevicePage:
        self.calls.append((search, page, per_page))
        return DevicePage([DeviceListing(DEVICE, "HP Inc.", "printer")], page, per_page, 1)


class FakeDeviceDetail:
    """DeviceDetailService: every device but 99 has DETAIL and HISTORY. Remembers the history paging."""
    MISSING_ID = 99

    def __init__(self) -> None:
        self.history_calls: list[tuple[int, int, int]] = []

    def get_device_detail(self, device_id: int) -> DeviceDetail | None:
        return None if device_id == self.MISSING_ID else DETAIL

    def get_device_history(self, device_id: int, page: int = 1, per_page: int = 50) -> DeviceHistoryPage | None:
        self.history_calls.append((device_id, page, per_page))
        if device_id == self.MISSING_ID:
            return None
        first = (page - 1) * per_page
        return DeviceHistoryPage(tuple(HISTORY[first:first + per_page]), page, per_page, len(HISTORY))


def make_client(user: User | None = VIEWER, *, devices: FakeSearch | None = None,
                detail: FakeDeviceDetail | None = None, scans: FakeScans | None = None) -> FlaskClient:
    """Signed in as `user` (a viewer by default: every route here is read-only); None: signed out."""
    users = FakeUsers(*([user] if user is not None else []))
    services = Services(scan_manager=FakeScanManager(), scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                        devices=devices or FakeSearch(), users=users, access=FakeAccess(users),
                        scans=scans or FakeScans(), device_maintenance=FakeLoad(),
                        device_detail=detail or FakeDeviceDetail())
    client = create_app(services, secret_key=SECRET_KEY).test_client()
    if user is not None:
        signed_in = client.post("/api/login", json={"username": user.username, "password": PASSWORD})
        assert signed_in.status_code == 200, signed_in.get_json()
    return client


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = FakeSearch()
        self.client = make_client(devices=self.devices)

    def test_each_parameter_reaches_the_service(self) -> None:
        query = ("type=4&vendor=12&status=online&flagged=true&trusted=false&confidence_min=10&confidence_max=90"
                 "&seen_from=2026-11-01&seen_to=2026-11-08&q=printer&sort=ip&order=asc&page=2&per_page=10")
        response = self.client.get(f"/api/devices?{query}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.devices.calls, [(DeviceSearch(
            device_type_id=4, vendor_id=12, network_status=NetworkStatus.ONLINE, flagged_status=True,
            trusted_status=False, confidence_min=10, confidence_max=90, seen_from=date(2026, 11, 1),
            seen_to=date(2026, 11, 8), text="printer", sort="ip", descending=False), 2, 10)])

    def test_the_answer_has_51s_shape(self) -> None:
        body = self.client.get("/api/devices?q=printer").get_json()
        self.assertEqual(set(body), {"devices", "page", "per_page", "total"})
        self.assertEqual(body["devices"][0]["name"], "Office printer")

    def test_no_parameters_is_an_empty_search(self) -> None:
        self.client.get("/api/devices")
        self.assertEqual(self.devices.calls, [(DeviceSearch(), 1, 50)])

    def test_bad_values_are_400_with_every_reason_and_nothing_searched(self) -> None:
        response = self.client.get("/api/devices?type=abc&flagged=maybe&seen_to=7th&sort=colour")
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body["error"], "The search can't be run.")
        self.assertEqual(len(body["reasons"]), 4)
        for name in ("type", "flagged", "seen_to", "sort"):
            self.assertTrue(any(reason.startswith(name) for reason in body["reasons"]), name)
        self.assertEqual(self.devices.calls, [])

    def test_bad_paging_is_still_51s_400(self) -> None:
        response = self.client.get("/api/devices?per_page=201&q=printer")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "per_page must be a whole number from 1 to 200, not '201'."})
        self.assertEqual(self.devices.calls, [])


class DetailTests(unittest.TestCase):
    def test_detail_shape(self) -> None:
        response = make_client().get("/api/devices/7")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(list(body), ["device", "loaded_at", "inferred", "score", "conflicting"])
        self.assertEqual(body["loaded_at"], "2026-11-08T10:15:02")
        self.assertEqual(body["device"]["device_type_id"], 4)       # the edit form's fields are all there
        self.assertEqual({key: body[key] for key in DETAIL_JSON}, DETAIL_JSON)

    def test_detail_marks_the_gateway_and_reads_the_latest_scan_once(self) -> None:
        scans = FakeScans(DEVICE.mac_address.lower())
        body = make_client(scans=scans).get("/api/devices/7").get_json()
        self.assertIs(body["device"]["is_gateway"], True)
        self.assertEqual(scans.latest_gateway_calls, 1)

    def test_unknown_id_is_404(self) -> None:
        response = make_client().get("/api/devices/99")
        self.assertEqual((response.status_code, response.get_json()), (404, MISSING))

    def test_deleted_between_the_two_reads_is_404(self) -> None:
        detail = FakeDeviceDetail()
        detail.get_device_detail = lambda device_id: None
        response = make_client(detail=detail).get("/api/devices/7")
        self.assertEqual(response.status_code, 404)

    def test_no_password_hash_anywhere(self) -> None:
        self.assertNotIn("not-a-real-hash", make_client().get("/api/devices/7").get_data(as_text=True))


class HistoryTests(unittest.TestCase):
    def test_history_shape_newest_first(self) -> None:
        body = make_client().get("/api/devices/7/history?page=1&per_page=2").get_json()
        self.assertEqual(body, {"observations": [
            {"scan_id": 12, "scan_started_at": "2026-11-08T09:12:00", "observed_at": "2026-11-08T09:12:05",
             "ip": "192.168.1.12", "status": "online"},
            {"scan_id": 11, "scan_started_at": "2026-11-08T09:11:00", "observed_at": "2026-11-08T09:11:05",
             "ip": "192.168.1.11", "status": "online"}], "page": 1, "per_page": 2, "total": 3})

    def test_defaults_are_page_1_of_50(self) -> None:
        detail = FakeDeviceDetail()
        make_client(detail=detail).get("/api/devices/7/history")
        self.assertEqual(detail.history_calls, [(7, 1, 50)])

    def test_unknown_id_is_404(self) -> None:
        response = make_client().get("/api/devices/99/history")
        self.assertEqual((response.status_code, response.get_json()), (404, MISSING))

    def test_bad_paging_is_400_and_nothing_read(self) -> None:
        for query in ("page=0", "per_page=201", "page=two"):
            with self.subTest(query=query):
                detail = FakeDeviceDetail()
                response = make_client(detail=detail).get(f"/api/devices/7/history?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(detail.history_calls, [])


class AccessTests(unittest.TestCase):
    URLS = ("/api/devices?q=printer", "/api/devices/7", "/api/devices/7/history")

    def test_a_viewer_may_use_all_three(self) -> None:
        client = make_client(VIEWER)
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_signed_out_is_401_and_nothing_read(self) -> None:
        devices, detail = FakeSearch(), FakeDeviceDetail()
        client = make_client(None, devices=devices, detail=detail)
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 401)
        self.assertEqual((devices.calls, detail.history_calls), ([], []))


if __name__ == "__main__":
    unittest.main()
