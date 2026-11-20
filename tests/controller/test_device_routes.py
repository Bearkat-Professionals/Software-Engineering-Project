# tests/controller/test_device_routes.py
"""Tests for the device record API (2.3.1), through Flask's test client with fake
services. No database.

    py -3.13 -m unittest tests.controller.test_device_routes -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

from flask.testing import FlaskClient

from backend.controller.app_controller import Services, create_app
from backend.records import Device, DeviceType, User, Vendor
from backend.records.enums import NetworkStatus, UserRole
from backend.services.records.device_maintenance import (DeviceEdit, DeviceMaintenanceResult,
                                                         DeviceMaintenanceStatus, LoadedDevice)
from backend.services.scan.scan_session import ScanState, ScanStatusReport
from tests.controller.test_api_routes import FakeDevices, FakeScanManager, FakeSessions
from tests.controller.test_search_detail_routes import DETAIL_JSON, FakeDeviceDetail
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers

SAM = User(user_id=4, username="sam", password_hash="not-a-real-hash", role=UserRole.EDITOR)
VIEWER = User(user_id=5, username="val", password_hash="not-a-real-hash", role=UserRole.VIEWER)
ADA = User(user_id=6, username="ada", password_hash="not-a-real-hash", role=UserRole.ADMIN)
AUTH_LOG = "backend.controller.auth"

LOADED = datetime(2026, 11, 8, 10, 15, 2)
LOADED_TEXT = "2026-11-08T10:15:02"
CHANGED = datetime(2026, 11, 8, 14, 5, 31)
RELOAD = {"error": "loaded_at is missing or unreadable. Reload the device and try again."}

# The ticket's example device, and its JSON word for word
DEVICE = Device(device_id=7, mac_address="F0:D1:A9:12:34:56", current_ip="192.168.1.23", hostname="NPI1A2B3C",
                device_type_id=4, vendor_id=12, device_name="Office printer", model="LaserJet 4000",
                trusted_status=True, confidence_score=20, network_status=NetworkStatus.ONLINE,
                first_seen=datetime(2026, 10, 26, 14, 3, 9), last_seen=datetime(2026, 11, 8, 9, 12, 44))
LOADED_DEVICE = LoadedDevice(DEVICE, LOADED, "HP Inc.", "printer")
DEVICE_JSON = {"device_id": 7, "name": "Office printer", "type": "printer", "vendor": "HP Inc.",
               "device_type_id": 4, "vendor_id": 12, "model": "LaserJet 4000",
               "ip": "192.168.1.23", "hostname": "NPI1A2B3C", "mac": "F0:D1:A9:12:34:56",
               "is_gateway": False, "is_randomized_mac": False, "network_status": "online", "confidence": 20,
               "flagged": False, "trusted": True, "mark": "trusted",
               "first_seen": "2026-10-26T14:03:09", "last_seen": "2026-11-08T09:12:44"}
REPLY = {"device": DEVICE_JSON, "loaded_at": LOADED_TEXT}


class FakeDeviceMaintenance:
    """DeviceMaintenance in memory. `answer` is what the next change returns (None: DONE);
    `changes` is every change call, so "nothing called" is changes == []."""

    def __init__(self) -> None:
        self.answer: DeviceMaintenanceResult | None = None
        self.devices: dict[int, LoadedDevice] = {7: LOADED_DEVICE}
        self.changes: list[tuple] = []

    def load(self, device_id: int) -> LoadedDevice:
        return self.devices.get(device_id, LoadedDevice(None, None))

    def add(self, mac: str, edit: DeviceEdit, user_id: int, *, ip: str | None = None,
            trusted: bool = False) -> DeviceMaintenanceResult:
        self.changes.append(("add", mac, edit, user_id, ip, trusted))
        return self._answer()

    def edit(self, device_id: int, edit: DeviceEdit, user_id: int, loaded_at: datetime | None) -> DeviceMaintenanceResult:
        self.changes.append(("edit", device_id, edit, user_id, loaded_at))
        return self._answer()

    def set_mark(self, device_id: int, mark: str, user_id: int, loaded_at: datetime | None) -> DeviceMaintenanceResult:
        self.changes.append(("mark", device_id, mark, user_id, loaded_at))
        return self._answer()

    def delete(self, device_id: int, user_id: int, loaded_at: datetime | None) -> DeviceMaintenanceResult:
        self.changes.append(("delete", device_id, user_id, loaded_at))
        return self.answer or DeviceMaintenanceResult(DeviceMaintenanceStatus.DONE, changed_by=user_id,
                                                      changed_at=LOADED,
                                                      removed={"observations_removed": 3, "evidence_removed": 2})

    def _answer(self) -> DeviceMaintenanceResult:
        return self.answer or DeviceMaintenanceResult(DeviceMaintenanceStatus.DONE, DEVICE,
                                                      changed_by=SAM.user_id, changed_at=LOADED)


class FakePickers:
    """The two Reference Maintenance reads the edit form's pickers use."""

    def __init__(self) -> None:
        self.types = [DeviceType(device_type_id=4, category_name="printer"),
                      DeviceType(device_type_id=1, category_name="unknown")]
        self.vendors = [Vendor(vendor_id=3, vendor_name="Apple, Inc."),
                        Vendor(vendor_id=88, vendor_name="Appleton Systems")]
        self.searches: list[tuple[str, int]] = []

    def active_device_types(self) -> list[DeviceType]:
        return list(self.types)

    def search_vendors(self, text: str, limit: int) -> list[Vendor]:
        self.searches.append((text, limit))
        return self.vendors[:limit]


def make_client(user: User = SAM, *, maintenance: FakeDeviceMaintenance | None = None,
                pickers: FakePickers | None = None) -> FlaskClient:
    users = FakeUsers(SAM, VIEWER, ADA)
    services = Services(scan_manager=FakeScanManager(),
                        scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                        devices=FakeDevices(), users=users, access=FakeAccess(users),
                        reference=pickers or FakePickers(), device_maintenance=maintenance or FakeDeviceMaintenance(),
                        device_detail=FakeDeviceDetail())
    client = create_app(services, secret_key=SECRET_KEY).test_client()
    signed_in = client.post("/api/login", json={"username": user.username, "password": PASSWORD})
    assert signed_in.status_code == 200, signed_in.get_json()
    return client


class RouteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.maintenance = FakeDeviceMaintenance()
        self.pickers = FakePickers()
        self.client = make_client(maintenance=self.maintenance, pickers=self.pickers)

    def assertNothingChanged(self) -> None:
        self.assertEqual(self.maintenance.changes, [])


# ---- reading -----------------------------------------------------------------------------

class GetDeviceTests(RouteTestCase):
    def test_get_returns_the_form_shape(self) -> None:
        response = self.client.get("/api/devices/7")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {**REPLY, **DETAIL_JSON})      # plus the detail (#121)

    def test_the_mark_follows_the_stored_flags(self) -> None:
        cases = (({"trusted_status": True}, "trusted"), ({"flag_set_by_user": True}, "flagged"), ({}, "automatic"))
        for fields, mark in cases:
            with self.subTest(mark=mark):
                device = Device(device_id=8, mac_address="F0:D1:A9:00:00:08", **fields)
                self.maintenance.devices[8] = LoadedDevice(device, LOADED, "Unknown", "Unknown")
                self.assertEqual(self.client.get("/api/devices/8").get_json()["device"]["mark"], mark)

    def test_a_missing_device_is_404(self) -> None:
        response = self.client.get("/api/devices/99")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {
            "error": "There is no device with id 99. It may have been deleted; reload the list."})

    def test_a_viewer_can_read(self) -> None:
        self.assertEqual(make_client(VIEWER).get("/api/devices/7").status_code, 200)


# ---- changing a device --------------------------------------------------------------------

class AddTests(RouteTestCase):
    def test_add_with_every_field(self) -> None:
        body = {"mac": "F0:D1:A9:12:34:56", "name": "Office printer", "device_type_id": 4, "vendor_id": 12,
                "model": "LaserJet 4000", "ip": "192.168.1.23", "trusted": True}
        response = self.client.post("/api/devices", json=body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), REPLY)                         # read back, same shape as GET
        edit = DeviceEdit(device_name="Office printer", device_type_id=4, vendor_id=12, model="LaserJet 4000")
        self.assertEqual(self.maintenance.changes,
                         [("add", "F0:D1:A9:12:34:56", edit, SAM.user_id, "192.168.1.23", True)])

    def test_only_the_mac_is_required(self) -> None:
        self.assertEqual(self.client.post("/api/devices", json={"mac": "F0:D1:A9:12:34:56"}).status_code, 201)
        self.assertEqual(self.maintenance.changes, [("add", "F0:D1:A9:12:34:56", DeviceEdit(), SAM.user_id, None, False)])

    def test_every_problem_with_the_fields_at_once_and_nothing_called(self) -> None:
        body = {"mac": 5, "name": None, "vendor_id": True, "trusted": "yes", "colour": "red"}
        response = self.client.post("/api/devices", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "The device can't be saved.", "reasons": [
            "'mac' must be text.", "'name' must be text.", "'vendor_id' must be a whole number.",
            "'trusted' must be true or false.",
            "'colour' can't be sent here. Send: mac, name, device_type_id, vendor_id, model, ip, trusted."]})
        self.assertNothingChanged()

    def test_a_missing_mac_or_a_body_that_isnt_an_object_is_400(self) -> None:
        response = self.client.post("/api/devices", json={"name": "Den TV"})
        self.assertEqual(response.get_json()["reasons"], ["'mac' is required."])
        self.assertEqual(self.client.post("/api/devices", json=["F0:D1:A9:12:34:56"]).status_code, 400)
        self.assertNothingChanged()

    def test_a_mac_that_already_exists_is_409_with_its_id(self) -> None:
        self.maintenance.answer = DeviceMaintenanceResult(DeviceMaintenanceStatus.ALREADY_EXISTS, existing_device_id=7,
            reasons=("A device with MAC F0:D1:A9:12:34:56 is already stored. Edit that one instead.",))
        response = self.client.post("/api/devices", json={"mac": "F0:D1:A9:12:34:56"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {
            "error": "A device with MAC F0:D1:A9:12:34:56 is already stored. Edit that one instead.", "device_id": 7})


class EditTests(RouteTestCase):
    def test_an_edit_sends_the_fields_and_loaded_at(self) -> None:
        response = self.client.put("/api/devices/7", json={"loaded_at": LOADED_TEXT, "name": "Front desk printer",
                                                           "vendor_id": 12})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), REPLY)
        self.assertEqual(self.maintenance.changes, [
            ("edit", 7, DeviceEdit(device_name="Front desk printer", vendor_id=12), SAM.user_id, LOADED)])

    def test_an_empty_string_reaches_the_service_as_clear(self) -> None:
        self.client.put("/api/devices/7", json={"loaded_at": LOADED_TEXT, "model": ""})
        self.assertEqual(self.maintenance.changes[0][2], DeviceEdit(model=""))     # "", not None (keep)

    def test_the_mac_and_ip_cant_be_edited(self) -> None:
        response = self.client.put("/api/devices/7", json={"loaded_at": LOADED_TEXT, "mac": "F0:D1:A9:00:00:01"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["reasons"],
                         ["'mac' can't be sent here. Send: name, device_type_id, vendor_id, model."])
        self.assertNothingChanged()

    def test_a_missing_or_unreadable_loaded_at_is_400(self) -> None:
        for loaded_at in (None, "yesterday", 5, "2026-11-08T10:15:02Z"):
            with self.subTest(loaded_at=loaded_at):
                body = {"name": "x"} if loaded_at is None else {"name": "x", "loaded_at": loaded_at}
                response = self.client.put("/api/devices/7", json=body)
                self.assertEqual((response.status_code, response.get_json()), (400, RELOAD))
        self.assertNothingChanged()


class MarkTests(RouteTestCase):
    def test_a_mark_is_sent_with_loaded_at(self) -> None:
        response = self.client.put("/api/devices/7/mark", json={"loaded_at": LOADED_TEXT, "mark": "flagged"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.maintenance.changes, [("mark", 7, "flagged", SAM.user_id, LOADED)])

    def test_a_missing_or_wrong_typed_mark_is_400(self) -> None:
        for body, reason in (({"loaded_at": LOADED_TEXT}, "'mark' is required."),
                             ({"loaded_at": LOADED_TEXT, "mark": 1}, "'mark' must be text.")):
            with self.subTest(reason=reason):
                self.assertEqual(self.client.put("/api/devices/7/mark", json=body).get_json()["reasons"], [reason])
        self.assertNothingChanged()

    def test_an_unknown_mark_is_the_services_400(self) -> None:
        self.maintenance.answer = DeviceMaintenanceResult(DeviceMaintenanceStatus.REJECTED,
            reasons=("'pinned' is not a mark. Use one of: trusted, flagged, automatic.",))
        response = self.client.put("/api/devices/7/mark", json={"loaded_at": LOADED_TEXT, "mark": "pinned"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["reasons"], ["'pinned' is not a mark. Use one of: trusted, flagged, automatic."])


class DeleteTests(RouteTestCase):
    def test_delete_returns_what_was_removed(self) -> None:
        response = self.client.delete(f"/api/devices/7?loaded_at={LOADED_TEXT}", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"deleted": 7, "observations_removed": 3, "evidence_removed": 2})
        self.assertEqual(self.maintenance.changes, [("delete", 7, SAM.user_id, LOADED)])

    def test_delete_needs_loaded_at_in_the_query(self) -> None:
        for url in ("/api/devices/7", "/api/devices/7?loaded_at=soon", "/api/devices/7?loaded_at=2026-11-08T10:15:02Z"):
            with self.subTest(url=url):
                response = self.client.delete(url, json={})
                self.assertEqual((response.status_code, response.get_json()), (400, RELOAD))
        self.assertNothingChanged()


# ---- refusals -----------------------------------------------------------------------------

class RefusalTests(RouteTestCase):
    def test_each_refusal_from_an_edit(self) -> None:
        cases = (
            (DeviceMaintenanceResult(DeviceMaintenanceStatus.REJECTED, reasons=("F0:D1 is not a valid MAC address.",
                                                                                 "The vendor is retired.")), 400,
             {"error": "The device can't be saved.",
              "reasons": ["F0:D1 is not a valid MAC address.", "The vendor is retired."]}),
            (DeviceMaintenanceResult(DeviceMaintenanceStatus.NOT_FOUND, reasons=("There is no device with id 7.",)), 404,
             {"error": "There is no device with id 7."}),
            (DeviceMaintenanceResult(DeviceMaintenanceStatus.CONFLICT, reasons=("x",), changed_by=ADA.user_id,
                                     changed_at=CHANGED), 409,
             {"error": "This device was changed by ada at 14:05 after you opened it. Reload to see their changes.",
              "changed_by": "ada", "changed_at": "2026-11-08T14:05:31"}),
        )
        for result, status, body in cases:
            with self.subTest(status=result.status.value):
                self.maintenance.answer = result
                response = self.client.put("/api/devices/7", json={"loaded_at": LOADED_TEXT, "name": "x"})
                self.assertEqual((response.status_code, response.get_json()), (status, body))

    def test_a_delete_conflict_names_who_and_when(self) -> None:
        self.maintenance.answer = DeviceMaintenanceResult(DeviceMaintenanceStatus.CONFLICT, changed_by=ADA.user_id,
                                                          changed_at=CHANGED)
        response = self.client.delete(f"/api/devices/7?loaded_at={LOADED_TEXT}", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["changed_by"], "ada")


# ---- the edit form's pickers --------------------------------------------------------------

class PickerTests(RouteTestCase):
    def test_device_types(self) -> None:
        response = self.client.get("/api/device-types")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"device_types": [{"device_type_id": 4, "name": "printer"},
                                                                {"device_type_id": 1, "name": "unknown"}]})

    def test_vendor_search(self) -> None:
        response = self.client.get("/api/vendors?search=%20app%20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"vendors": [{"vendor_id": 3, "name": "Apple, Inc."},
                                                           {"vendor_id": 88, "name": "Appleton Systems"}]})
        self.assertEqual(self.pickers.searches, [("app", 20)])            # trimmed, default limit

    def test_the_limit_reaches_the_service(self) -> None:
        self.client.get("/api/vendors?search=app&limit=50")
        self.assertEqual(self.pickers.searches, [("app", 50)])

    def test_too_short_text_or_a_bad_limit_is_400_and_nothing_is_searched(self) -> None:
        for query in ("", "search=a", "search=%20a%20", "search=" + "a" * 101, "search=app&limit=0",
                      "search=app&limit=51", "search=app&limit=ten"):
            with self.subTest(query=query[:30]):
                self.assertEqual(self.client.get(f"/api/vendors?{query}").status_code, 400)
        self.assertEqual(self.pickers.searches, [])

    def test_a_viewer_can_use_the_pickers(self) -> None:
        viewer = make_client(VIEWER)
        self.assertEqual(viewer.get("/api/device-types").status_code, 200)
        self.assertEqual(viewer.get("/api/vendors?search=app").status_code, 200)


# ---- who may change -----------------------------------------------------------------------

CHANGE_ROUTES = (
    ("POST", "/api/devices", {"mac": "F0:D1:A9:12:34:56"}),
    ("PUT", "/api/devices/7", {"loaded_at": LOADED_TEXT, "name": "x"}),
    ("PUT", "/api/devices/7/mark", {"loaded_at": LOADED_TEXT, "mark": "flagged"}),
    ("DELETE", f"/api/devices/7?loaded_at={LOADED_TEXT}", {}),
)


class ViewerTests(unittest.TestCase):
    def test_a_viewer_gets_403_on_every_change_route_logged_and_nothing_called(self) -> None:
        maintenance = FakeDeviceMaintenance()
        client = make_client(VIEWER, maintenance=maintenance)
        for method, url, body in CHANGE_ROUTES:
            with self.subTest(route=f"{method} {url}"):
                with self.assertLogs(AUTH_LOG, level="WARNING"):                 # AT-13
                    response = client.open(url, method=method, json=body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json(), {"error": "Your role (viewer) can't edit devices."})
        self.assertEqual(maintenance.changes, [])

    def test_an_admin_can_change(self) -> None:
        self.assertEqual(make_client(ADA).post("/api/devices", json={"mac": "F0:D1:A9:12:34:56"}).status_code, 201)


if __name__ == "__main__":
    unittest.main()
