"""Tests for the scan and device API (#51), through Flask's test client with fake
services. No database or network.

    python -m unittest tests.controller.test_api_routes -v
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import Any

from flask import Flask
from flask.testing import FlaskClient

from backend.controller.api_routes import STATUS_URL
from backend.controller.app_controller import Services, create_app
from backend.data_access.network_data import DeviceListing, DevicePage
from backend.records import Device, User
from backend.records.enums import NetworkStatus, ScanOutcome, UserRole
from backend.records.scan import Scan
from backend.services.scan.scan_session import (OpenRefusal, OpenResult, ScanPhase, ScanState,
                                                ScanStatusReport)

SCOPE = "192.168.1.0/24"
SAM = User(user_id=4, username="sam", password_hash="not-a-real-hash", role=UserRole.EDITOR)
VIEWER = User(user_id=5, username="val", password_hash="not-a-real-hash", role=UserRole.VIEWER)
STARTED = datetime(2026, 10, 26, 14, 3)
ROUTES_LOG = "backend.controller.api_routes"
PING_ERROR = "ping unavailable: ping command not found"


class FakeScanManager:
    """ScanManager.start without threads: answers with `answer` and remembers each call."""
    def __init__(self, answer: OpenResult | None = None) -> None:
        self.answer = answer or OpenResult(True, scan=Scan(scan_id=12, user_id=SAM.user_id, target_scope=SCOPE))
        self.calls: list[tuple[str, int]] = []

    def start(self, scope: str, user_id: int) -> OpenResult:
        self.calls.append((scope, user_id))
        return self.answer


class FakeSessions:
    def __init__(self, report: ScanStatusReport) -> None:
        self.report = report

    def status(self) -> ScanStatusReport:
        return self.report


class FakeDevices:
    """DeviceStore.list_page in memory, with its order: newest last_seen first, then highest id."""
    def __init__(self, listings: list[DeviceListing] = ()) -> None:
        self.listings = list(listings)
        self.calls: list[tuple[int, int]] = []

    def list_page(self, page: int = 1, per_page: int = 50) -> DevicePage:
        self.calls.append((page, per_page))
        ordered = sorted(self.listings, key=lambda item: (item.device.last_seen, item.device.device_id),
                         reverse=True)
        start = (page - 1) * per_page
        return DevicePage(ordered[start:start + per_page], page, per_page, len(ordered))


def listing(device_id: int, seen_minutes_ago: int, **fields: Any) -> DeviceListing:
    device = Device(device_id=device_id, mac_address=f"F0:D1:A9:00:00:{device_id:02X}",
                    current_ip=f"192.168.1.{device_id}", vendor_id=3, device_type_id=1,
                    last_seen=STARTED - timedelta(minutes=seen_minutes_ago),
                    network_status=NetworkStatus.ONLINE, **fields)
    return DeviceListing(device, "Apple, Inc.", "unknown")


def make_client(user: User | None = SAM, *, manager: FakeScanManager | None = None,
                report: ScanStatusReport | None = None,
                devices: FakeDevices | None = None) -> FlaskClient:
    services = Services(scan_manager=manager or FakeScanManager(),
                        scan_sessions=FakeSessions(report or ScanStatusReport(state=ScanState.NONE)),
                        devices=devices or FakeDevices(), users=None)
    app: Flask = create_app(services, user)
    return app.test_client()


class StartScanTests(unittest.TestCase):
    def test_started_is_202_with_the_scan_id_and_where_to_follow_it(self) -> None:
        response = make_client().post("/api/scans", json={"scope": SCOPE})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {"scan_id": 12, "status_url": STATUS_URL})

    def test_the_scan_is_started_as_the_calling_user(self) -> None:
        manager = FakeScanManager()
        make_client(manager=manager).post("/api/scans", json={"scope": SCOPE})
        self.assertEqual(manager.calls, [(SCOPE, SAM.user_id)])

    def test_each_refusal_gets_its_status_and_the_services_words(self) -> None:
        cases = (
            (OpenRefusal.ALREADY_RUNNING, "A scan started by sam at 14:03 is still running.", 409),
            (OpenRefusal.INVALID_SCOPE, "10.0.0.0/8 is too big. Use a range of /22 or smaller, like /24.", 400),
            (OpenRefusal.UNKNOWN_USER, "Your account was not found. Sign in again.", 401),
            (OpenRefusal.SCAN_STARTING, "Another scan is starting. Try again in a moment.", 409),
        )
        for refusal, reason, status in cases:
            with self.subTest(refusal=refusal):
                manager = FakeScanManager(OpenResult(False, reason=reason, refusal=refusal))
                response = make_client(manager=manager).post("/api/scans", json={"scope": SCOPE})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.get_json(), {"error": reason})

    def test_missing_or_wrong_body_is_400_and_starts_nothing(self) -> None:
        bodies = {
            "no body": {},
            "not JSON": {"data": "scope=192.168.1.0/24", "content_type": "text/plain"},
            "no scope": {"json": {}},
            "scope not text": {"json": {"scope": 24}},
            "a list": {"json": [SCOPE]},
        }
        for name, request in bodies.items():
            with self.subTest(name):
                manager = FakeScanManager()
                response = make_client(manager=manager).post("/api/scans", **request)
                self.assertEqual(response.status_code, 400)
                self.assertIn('{"scope": "192.168.1.0/24"}', response.get_json()["error"])
                self.assertEqual(manager.calls, [])

    def test_a_viewer_may_not_start_a_scan(self) -> None:
        manager = FakeScanManager()
        response = make_client(VIEWER, manager=manager).post("/api/scans", json={"scope": SCOPE})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "Your role (viewer) can't start scans."})
        self.assertEqual(manager.calls, [])

    def test_nobody_signed_in_is_401(self) -> None:
        manager = FakeScanManager()
        response = make_client(None, manager=manager).post("/api/scans", json={"scope": SCOPE})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(manager.calls, [])

    def test_a_refusal_is_logged_with_who_and_what_but_not_the_hash(self) -> None:
        with self.assertLogs(ROUTES_LOG, level="WARNING") as logs:
            make_client(VIEWER).post("/api/scans", json={"scope": SCOPE})
        self.assertEqual(len(logs.output), 1)
        for expected in ("val", "POST", "/api/scans", "START_SCAN"):
            self.assertIn(expected, logs.output[0])
        self.assertNotIn(VIEWER.password_hash, logs.output[0])

        with self.assertLogs(ROUTES_LOG, level="WARNING") as logs:
            make_client(None).get("/api/devices")
        self.assertIn("nobody signed in", logs.output[0])

    def test_an_allowed_request_logs_nothing(self) -> None:
        with self.assertNoLogs(ROUTES_LOG, level="WARNING"):
            make_client().post("/api/scans", json={"scope": SCOPE})


class ScanStatusTests(unittest.TestCase):
    def status(self, report: ScanStatusReport, user: User | None = SAM) -> Any:
        response = make_client(user, report=report).get("/api/scans/status")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_running_shape(self) -> None:
        report = ScanStatusReport(state=ScanState.RUNNING, scan_id=12, scope=SCOPE, started_by="sam",
                                  started_at=STARTED, seconds_elapsed=6, phase=ScanPhase.DISCOVERING_HOSTS,
                                  done=120, total=254, errors=(PING_ERROR,))
        self.assertEqual(self.status(report), {
            "running": True, "scan_id": 12, "scope": SCOPE,
            "started_by": "sam", "started_at": "2026-10-26T14:03:00", "elapsed_seconds": 6,
            "phase": "discovering_hosts", "done": 120, "total": 254, "errors": [PING_ERROR]})

    def test_finished_shape(self) -> None:
        report = ScanStatusReport(state=ScanState.FINISHED, scan_id=12, scope=SCOPE, started_by="sam",
                                  started_at=STARTED, ended_at=STARTED + timedelta(seconds=11),
                                  outcome=ScanOutcome.PARTIAL, errors=(PING_ERROR,))
        self.assertEqual(self.status(report), {"running": False, "last_scan": {
            "scan_id": 12, "scope": SCOPE, "started_by": "sam", "started_at": "2026-10-26T14:03:00",
            "ended_at": "2026-10-26T14:03:11", "outcome": "partial", "errors": [PING_ERROR]}})

    def test_no_scans_yet_shape(self) -> None:
        self.assertEqual(self.status(ScanStatusReport(state=ScanState.NONE)),
                         {"running": False, "last_scan": None})

    def test_a_viewer_may_see_the_status(self) -> None:
        self.assertEqual(self.status(ScanStatusReport(state=ScanState.NONE), VIEWER)["running"], False)


class DeviceListTests(unittest.TestCase):
    def get(self, url: str, devices: FakeDevices) -> Any:
        return make_client(devices=devices).get(url)

    def test_one_device_with_names_not_ids(self) -> None:
        devices = FakeDevices([listing(7, 0, confidence_score=20, flagged_status=True)])
        body = self.get("/api/devices", devices).get_json()
        self.assertEqual(body, {"devices": [{
            "device_id": 7, "name": None, "type": "unknown", "vendor": "Apple, Inc.",
            "ip": "192.168.1.7", "mac": "F0:D1:A9:00:00:07", "is_randomized_mac": False,
            "network_status": "online", "confidence": 20, "flagged": True, "trusted": False,
            "last_seen": "2026-10-26T14:03:00"}], "page": 1, "per_page": 50, "total": 1})

    def test_a_blank_name_is_null_and_a_real_name_is_kept(self) -> None:
        devices = FakeDevices([listing(1, 0, device_name=""), listing(2, 1, device_name="Printer")])
        names = [device["name"] for device in self.get("/api/devices", devices).get_json()["devices"]]
        self.assertEqual(names, [None, "Printer"])

    def test_newest_sighting_first(self) -> None:
        devices = FakeDevices([listing(1, 30), listing(2, 0), listing(3, 10)])
        ids = [device["device_id"] for device in self.get("/api/devices", devices).get_json()["devices"]]
        self.assertEqual(ids, [2, 3, 1])

    def test_paging(self) -> None:
        devices = FakeDevices([listing(device_id, device_id) for device_id in range(1, 6)])   # 1 is newest
        body = self.get("/api/devices?page=2&per_page=2", devices).get_json()
        self.assertEqual([device["device_id"] for device in body["devices"]], [3, 4])
        self.assertEqual((body["page"], body["per_page"], body["total"]), (2, 2, 5))
        self.assertEqual(devices.calls, [(2, 2)])

    def test_defaults_are_page_1_of_50(self) -> None:
        devices = FakeDevices()
        self.get("/api/devices", devices)
        self.assertEqual(devices.calls, [(1, 50)])

    def test_the_largest_page_size_is_allowed(self) -> None:
        devices = FakeDevices()
        self.assertEqual(self.get("/api/devices?per_page=200", devices).status_code, 200)
        self.assertEqual(devices.calls, [(1, 200)])

    def test_no_devices_is_an_empty_list(self) -> None:
        self.assertEqual(self.get("/api/devices", FakeDevices()).get_json(),
                         {"devices": [], "page": 1, "per_page": 50, "total": 0})

    def test_bad_paging_values_are_400_with_a_reason(self) -> None:
        for query in ("page=abc", "page=0", "page=-1", "page=1.5", "page=", "page=1000001",
                      "per_page=0", "per_page=201", "per_page=ten"):
            with self.subTest(query=query):
                devices = FakeDevices()
                response = self.get(f"/api/devices?{query}", devices)
                self.assertEqual(response.status_code, 400)
                self.assertIn(query.split("=")[0], response.get_json()["error"])
                self.assertEqual(devices.calls, [])


if __name__ == "__main__":
    unittest.main()
