"""Tests for the report and export API (#156): GET /api/scans, GET /api/reports/changes and
GET /api/export, through Flask's test client. The change report and the export are the REAL
services over fake stores, so their refusals are their own. No database or network.

    python -m unittest tests.controller.test_report_routes -v
"""
from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
from unittest import mock

from flask.testing import FlaskClient

from backend.controller.app_controller import Services, create_app
from backend.data_access.network_data import DeviceSearch
from backend.records import User
from backend.records.enums import NetworkStatus, UserRole
from backend.services.access_control import permissions
from backend.services.access_control.permissions import Permission
from backend.services.reporting.change_report import ChangeReportService
from backend.services.reporting.export_json import FORMAT, MAX_EXPORT_DEVICES
from backend.services.scan.scan_session import ScanState, ScanStatusReport
from tests.controller.test_api_routes import FakeScanManager, FakeSessions
from tests.controller.test_search_detail_routes import FakeSearch
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers
from tests.services.reporting.test_change_report import MONDAY, TUESDAY, FakeScans, scan, seen
from tests.services.reporting.test_export_json import ECHO, HASH, make_service

VIEWER = User(user_id=5, username="val", password_hash=HASH, role=UserRole.VIEWER)
FILE_NAME = r'attachment; filename="{prefix}-\d{{4}}-\d{{2}}-\d{{2}}T\d{{4}}Z\.json"'

# Two scans of 192.168.1.0/24: device 1 moved, 2 is new, 3 is gone, 4 was outside the later range,
# 5 is a new phone with a randomized MAC
SCANS = [scan(1, MONDAY), scan(2, TUESDAY), scan(3, datetime(2026, 11, 18, 9), outcome=None)]   # 3: unfinished
ROWS = [seen(1, 1, "192.168.1.5", "printer"), seen(2, 1, "192.168.1.9", "printer"),
        seen(2, 2, "192.168.1.40", first_seen=TUESDAY),
        seen(1, 3, "192.168.1.23"),
        seen(1, 4, "10.0.0.5"),
        seen(2, 5, "192.168.1.52", randomized=True)]


class ReportScans(FakeScans):
    """test_change_report's fake ScanStore, plus the gateway read the device list makes."""
    def latest_gateway_mac(self, unit: Any = None) -> str | None:
        return None


class RecordingExport:
    """The real ExportService (over test_export_json's fakes), remembering each DeviceSearch it got."""
    def __init__(self, **options: Any) -> None:
        self.service = make_service(**options)
        self.searches: list[DeviceSearch] = []

    def export_devices(self, search: DeviceSearch, exported_by: User):
        self.searches.append(search)
        return self.service.export_devices(search, exported_by)

    def export_scan(self, scan_id: int, exported_by: User):
        return self.service.export_scan(scan_id, exported_by)


@contextmanager
def unit_of_work():
    yield "unit"


def make_client(user: User | None = VIEWER, *, scans: ReportScans | None = None, export: RecordingExport | None = None,
                devices: FakeSearch | None = None) -> FlaskClient:
    """Signed in as `user` (a viewer by default); None: signed out."""
    users = FakeUsers(*([user] if user is not None else []))
    scans = scans or ReportScans(SCANS, ROWS)
    services = Services(scan_manager=FakeScanManager(), scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                        devices=devices or FakeSearch(), users=users, access=FakeAccess(users), scans=scans,
                        change_report=ChangeReportService(scans, unit_of_work), export=export or RecordingExport())
    client = create_app(services, secret_key=SECRET_KEY).test_client()
    if user is not None:
        signed_in = client.post("/api/login", json={"username": user.username, "password": PASSWORD})
        assert signed_in.status_code == 200, signed_in.get_json()
    return client


class ScanListTests(unittest.TestCase):
    def test_finished_scans_only_newest_first(self) -> None:
        body = make_client().get("/api/scans").get_json()
        self.assertEqual(body, {"scans": [
            {"scan_id": 2, "scope": "192.168.1.0/24", "started_by": "admin", "started_at": "2026-11-17T09:00:00",
             "ended_at": None, "outcome": "success", "devices_seen": 0},
            {"scan_id": 1, "scope": "192.168.1.0/24", "started_by": "admin", "started_at": "2026-11-16T09:00:00",
             "ended_at": None, "outcome": "success", "devices_seen": 0}], "page": 1, "per_page": 20, "total": 2})

    def test_paging(self) -> None:
        body = make_client().get("/api/scans?page=2&per_page=1").get_json()
        self.assertEqual(([row["scan_id"] for row in body["scans"]], body["total"]), ([1], 2))
        self.assertEqual(make_client().get("/api/scans?per_page=201").status_code, 400)


class ChangeReportTests(unittest.TestCase):
    def test_the_report_shape(self) -> None:
        body = make_client().get("/api/reports/changes?from=1&to=2").get_json()
        self.assertEqual(list(body), ["from_scan", "to_scan", "new", "missing", "changed", "outside_scope",
                                      "randomized", "partial_warning"])
        self.assertEqual(body["from_scan"], {"scan_id": 1, "scope": "192.168.1.0/24",
                                             "started_at": "2026-11-16T09:00:00", "outcome": "success"})
        self.assertEqual([device["device_id"] for device in body["new"]], [2, 5])
        self.assertEqual([device["device_id"] for device in body["missing"]], [3])
        self.assertEqual(body["changed"], [{
            "device_id": 1, "name": "device 1", "type": "printer", "vendor": "Acme", "mac": "F0:D1:A9:00:00:01",
            "is_randomized_mac": False, "old_ip": "192.168.1.5", "new_ip": "192.168.1.9",
            "old_hostname": "printer", "new_hostname": "printer", "first_seen_in_this_scan": False}])
        self.assertEqual((body["outside_scope"], body["randomized"], body["partial_warning"]),
                         (1, {"new": 1, "missing": 0, "changed": 0}, False))

    def test_neither_compares_the_latest_two(self) -> None:
        body = make_client().get("/api/reports/changes").get_json()
        self.assertEqual((body["from_scan"]["scan_id"], body["to_scan"]["scan_id"]), (1, 2))

    def test_only_one_of_from_and_to_is_400(self) -> None:
        for query in ("from=1", "to=2"):
            with self.subTest(query=query):
                response = make_client().get(f"/api/reports/changes?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertIn("Choose both scans", response.get_json()["error"])

    def test_unknown_or_unfinished_scan_is_404(self) -> None:
        for query in ("from=1&to=99", "from=1&to=3"):
            with self.subTest(query=query):
                response = make_client().get(f"/api/reports/changes?{query}")
                self.assertEqual(response.status_code, 404)
                self.assertIn("doesn't exist or hasn't finished", response.get_json()["error"])

    def test_other_refusals_are_400_with_the_services_reason(self) -> None:
        cases = {"from=2&to=2": "not scan 2 twice", "from=2&to=1": "the earlier scan goes first",
                 "from=abc&to=2": "from must be a whole number"}
        for query, reason in cases.items():
            with self.subTest(query=query):
                response = make_client().get(f"/api/reports/changes?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertIn(reason, response.get_json()["error"])

    def test_fewer_than_two_finished_scans_is_400(self) -> None:
        response = make_client(scans=ReportScans([scan(1, MONDAY)])).get("/api/reports/changes")
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs two finished scans", response.get_json()["error"])


class ExportTests(unittest.TestCase):
    def assertDownload(self, response, prefix: str) -> dict[str, Any]:
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertRegex(response.headers["Content-Disposition"], FILE_NAME.format(prefix=prefix))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return json.loads(response.get_data(as_text=True))

    def test_devices_download(self) -> None:
        document = self.assertDownload(make_client().get("/api/export?scope=devices"), "devices")
        self.assertEqual((document["format"], document["exported_by"], document["device_count"]), (FORMAT, "val", 2))
        self.assertEqual(document["scope"]["kind"], "devices")

    def test_scan_download(self) -> None:
        document = self.assertDownload(make_client().get("/api/export?scope=scan&scan_id=9"), "scan-9")
        self.assertEqual((document["scope"], document["scan"]["scan_id"]), ({"kind": "scan", "scan_id": 9}, 9))

    def test_the_file_name_uses_the_documents_own_time(self) -> None:
        response = make_client().get("/api/export?scope=devices")
        exported_at = json.loads(response.get_data(as_text=True))["exported_at"]       # "2026-11-19T20:05:31Z"
        stamp = exported_at[:13] + exported_at[14:16] + "Z"
        self.assertIn(f'filename="devices-{stamp}.json"', response.headers["Content-Disposition"])

    def test_every_filter_reaches_the_service(self) -> None:
        export = RecordingExport()
        query = ("type_id=4&vendor_id=12&status=online&flagged=true&trusted=false&randomized=true"
                 "&confidence_min=10&confidence_max=90&seen_from=2026-11-01&seen_to=2026-11-08&q=echo"
                 "&sort=ip&order=asc")
        self.assertEqual(make_client(export=export).get(f"/api/export?scope=devices&{query}").status_code, 200)
        self.assertEqual(export.searches, [DeviceSearch(
            device_type_id=4, vendor_id=12, network_status=NetworkStatus.ONLINE, flagged_status=True,
            trusted_status=False, is_randomized_mac=True, confidence_min=10, confidence_max=90,
            seen_from=date(2026, 11, 1), seen_to=date(2026, 11, 8), text="echo", sort="ip", descending=False)])

    def test_the_screens_short_names_still_work(self) -> None:
        export = RecordingExport()
        make_client(export=export).get("/api/export?scope=devices&type=4&vendor=12")
        self.assertEqual(export.searches, [DeviceSearch(device_type_id=4, vendor_id=12)])

    def test_each_bad_filter_is_400_naming_the_field_and_nothing_exported(self) -> None:
        bad = {"type_id": "x", "vendor_id": "0", "status": "away", "flagged": "maybe", "trusted": "yes",
               "randomized": "1", "confidence_min": "500", "confidence_max": "-1", "seen_from": "yesterday",
               "seen_to": "7th", "sort": "size", "order": "up"}
        for name, value in bad.items():
            with self.subTest(field=name):
                export = RecordingExport()
                response = make_client(export=export).get(f"/api/export?scope=devices&{name}={value}")
                self.assertEqual(response.status_code, 400)
                body = response.get_json()
                self.assertEqual(body["error"], "The search can't be run.")
                self.assertTrue(body["reasons"][0].startswith(f"{name} "), body["reasons"])
                self.assertEqual(export.searches, [])

    def test_type_and_type_id_that_disagree_is_400(self) -> None:
        response = make_client().get("/api/export?scope=devices&type=4&type_id=5")
        self.assertEqual(response.status_code, 400)
        self.assertIn("type and type_id are the same filter", response.get_json()["reasons"][0])

    def test_bad_requests_are_400(self) -> None:
        cases = {"": "scope must be devices or scan", "scope=all": "scope must be devices or scan",
                 "scope=scan": "scan_id is required", "scope=scan&scan_id=abc": "scan_id must be a whole number"}
        for query, reason in cases.items():
            with self.subTest(query=query):
                response = make_client().get(f"/api/export?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertIn(reason, response.get_json()["error"])

    def test_over_the_limit_is_400_with_the_services_reason(self) -> None:
        export = RecordingExport(listings=[ECHO] * (MAX_EXPORT_DEVICES + 1))
        response = make_client(export=export).get("/api/export?scope=devices")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Narrow the filters", response.get_json()["error"])

    def test_unknown_scan_is_404(self) -> None:
        response = make_client().get("/api/export?scope=scan&scan_id=99")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Scan 99 doesn't exist or hasn't finished."})


class DeviceListFilterTests(unittest.TestCase):
    def test_the_new_names_reach_the_device_list_too(self) -> None:
        devices = FakeSearch()
        response = make_client(devices=devices).get("/api/devices?type_id=4&randomized=false")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(devices.calls, [(DeviceSearch(device_type_id=4, is_randomized_mac=False), 1, 50)])


class AccessTests(unittest.TestCase):
    URLS = ("/api/scans", "/api/reports/changes", "/api/export?scope=devices", "/api/export?scope=scan&scan_id=9")

    def test_signed_out_is_401_and_nothing_exported(self) -> None:
        export = RecordingExport()
        client = make_client(None, export=export)
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 401)
        self.assertEqual(export.searches, [])

    def test_a_viewer_may_use_all_three(self) -> None:          # every role has EXPORT (#86)
        client = make_client()
        for url in self.URLS:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_without_export_the_export_is_403_but_the_reports_are_not(self) -> None:
        no_export = {UserRole.VIEWER: frozenset({Permission.VIEW_DATA})}       # no role lacks it today
        export = RecordingExport()
        with mock.patch.dict(permissions.ROLE_PERMISSIONS, no_export), self.assertLogs("backend.controller.auth", "WARNING"):
            client = make_client(export=export)
            for url in self.URLS[2:]:
                with self.subTest(url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.get_json(), {"error": "Your role (viewer) can't export data."})
            self.assertEqual(client.get("/api/scans").status_code, 200)
        self.assertEqual(export.searches, [])


class NoSecretsTests(unittest.TestCase):
    """AT-10 at the route level. A plain search for "sid" would also hit "outside_scope", so the
    keys are checked, and the real secrets (the hash, the password, the session cookie) are
    looked for as text."""

    def test_no_body_has_a_password_hash_or_session_id(self) -> None:
        client = make_client()
        cookie = client.get_cookie("session").value
        urls = AccessTests.URLS + ("/api/reports/changes?from=1&to=2",)
        for url in urls:
            with self.subTest(url=url):
                text = client.get(url).get_data(as_text=True)
                for secret in (HASH, PASSWORD, cookie, "letmein", "k3J9"):    # the last two hide in scan 9's config
                    self.assertNotIn(secret, text)
                for key in _keys(json.loads(text)):
                    self.assertNotRegex(key.lower(), r"password|hash|secret|^sid$")


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [key for key in value] + [key for item in value.values() for key in _keys(item)]
    if isinstance(value, list):
        return [key for item in value for key in _keys(item)]
    return []


if __name__ == "__main__":
    unittest.main()
