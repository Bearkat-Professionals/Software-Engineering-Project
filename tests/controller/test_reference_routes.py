# tests/controller/test_reference_routes.py
"""Tests for the reference data API (#126), through Flask's test client with a fake
Reference Maintenance service. No database.

    py -3.13 -m unittest tests.controller.test_reference_routes -v
"""
from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

from flask.testing import FlaskClient

from backend.controller.app_controller import Services, create_app
from backend.records import DeviceSignature, DeviceType, OuiPrefix, User, Vendor
from backend.records.enums import EvidenceType, UserRole
from backend.services.records.reference_maintenance import (MaintenanceResult, MaintenanceStatus,
                                                            ReferenceDetail, ReferenceKind,
                                                            ReferenceListing)
from tests.controller.test_api_routes import FakeDevices, FakeScanManager, FakeSessions
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers
from backend.services.scan.scan_session import ScanState, ScanStatusReport

SAM = User(user_id=4, username="sam", password_hash="not-a-real-hash", role=UserRole.EDITOR)
VIEWER = User(user_id=5, username="val", password_hash="not-a-real-hash", role=UserRole.VIEWER)
ADA = User(user_id=6, username="ada", password_hash="not-a-real-hash", role=UserRole.ADMIN)
AUTH_LOG = "backend.controller.auth"

VENDOR, DEVICE_TYPE, SIGNATURE = ReferenceKind.VENDOR, ReferenceKind.DEVICE_TYPE, ReferenceKind.SIGNATURE
URL = {VENDOR: "vendors", DEVICE_TYPE: "device-types", SIGNATURE: "signatures"}
ID = {VENDOR: 3, DEVICE_TYPE: 4, SIGNATURE: 9}

LOADED = datetime(2026, 11, 11, 10, 15, 2)
LOADED_TEXT = "2026-11-11T10:15:02"
CHANGED = datetime(2026, 11, 11, 14, 5, 31)

RECORDS = {
    VENDOR: Vendor(vendor_id=3, vendor_name="Apple, Inc."),
    DEVICE_TYPE: DeviceType(device_type_id=4, category_name="smart speaker"),
    SIGNATURE: DeviceSignature(signature_id=9, device_type_id=4, pattern_value="echo",
                               evidence_type=EvidenceType.MDNS, resulting_device_name="Amazon Echo"),
}
DETAILS = {
    VENDOR: ReferenceDetail(RECORDS[VENDOR], {"device": 12}, "12 devices use this vendor.", LOADED,
                            prefixes=("3C:07:54", "F0:D1:A9")),
    DEVICE_TYPE: ReferenceDetail(RECORDS[DEVICE_TYPE], {"device": 12, "device_signature": 3},
                                 "12 devices and 3 signatures use this device type.", LOADED),
    SIGNATURE: ReferenceDetail(RECORDS[SIGNATURE], {"identification_evidence": 5},
                               "5 device identifications use this signature.", LOADED,
                               device_type_name="smart speaker"),
}
DETAIL_JSON = {
    VENDOR: {"record": {"id": 3, "name": "Apple, Inc.", "status": "active"},
             "usage": {"device": 12}, "usage_text": "12 devices use this vendor.",
             "prefixes": ["3C:07:54", "F0:D1:A9"], "loaded_at": LOADED_TEXT},
    DEVICE_TYPE: {"record": {"id": 4, "name": "smart speaker", "status": "active"},
                  "usage": {"device": 12, "device_signature": 3},
                  "usage_text": "12 devices and 3 signatures use this device type.", "loaded_at": LOADED_TEXT},
    SIGNATURE: {"record": {"id": 9, "evidence_type": "mdns", "pattern": "echo", "device_type_id": 4,
                           "device_type": "smart speaker", "resulting_device_name": "Amazon Echo",
                           "resulting_model": None, "status": "active"},
                "usage": {"identification_evidence": 5},
                "usage_text": "5 device identifications use this signature.", "loaded_at": LOADED_TEXT},
}

# What a screen sends (the API's names) and what the service must get (its names)
ADD_BODY = {
    VENDOR: {"name": "Acme"},
    DEVICE_TYPE: {"name": "printer"},
    SIGNATURE: {"evidence_type": "mdns", "pattern": "echo", "device_type_id": 4,
                "resulting_device_name": "Amazon Echo", "resulting_model": None},
}
SERVICE_VALUES = {
    VENDOR: {"vendor_name": "Acme"},
    DEVICE_TYPE: {"category_name": "printer"},
    SIGNATURE: {"evidence_type": "mdns", "pattern_value": "echo", "device_type_id": 4,
                "resulting_device_name": "Amazon Echo", "resulting_model": None},
}
PREFIX = OuiPrefix(oui_id=50, oui_prefix="F0:D1:A9", vendor_id=3)


class FakeReference:
    """ReferenceMaintenance in memory. `answer` is what the next change returns (None: DONE);
    `changes` is every change call, so "nothing called" is changes == []."""

    def __init__(self) -> None:
        self.answer: MaintenanceResult | None = None
        self.details: dict[tuple[ReferenceKind, int], ReferenceDetail] = {
            (kind, ID[kind]): detail for kind, detail in DETAILS.items()}
        self.listing = ReferenceListing([RECORDS[VENDOR]], 1, 50, 1, {})
        self.changes: list[tuple] = []
        self.lists: list[tuple] = []

    def list_page(self, kind: ReferenceKind, page: int, per_page: int, *, text: str | None = None,
                  status: str | None = None) -> ReferenceListing:
        self.lists.append((kind, page, per_page, text, status))
        return self.listing

    def load(self, kind: ReferenceKind, record_id: int) -> ReferenceDetail | None:
        return self.details.get((kind, record_id))

    def add(self, kind: ReferenceKind, values: dict, user_id: int) -> MaintenanceResult:
        self.changes.append(("add", kind, values, user_id))
        return self._answer(RECORDS[kind])

    def edit(self, kind: ReferenceKind, record_id: int, values: dict, user_id: int,
             loaded_at: datetime | None) -> MaintenanceResult:
        self.changes.append(("edit", kind, record_id, values, user_id, loaded_at))
        return self._answer(RECORDS[kind])

    def retire(self, kind: ReferenceKind, record_id: int, user_id: int, loaded_at: datetime | None) -> MaintenanceResult:
        self.changes.append(("retire", kind, record_id, user_id, loaded_at))
        return self._answer(RECORDS[kind])

    def reactivate(self, kind: ReferenceKind, record_id: int, user_id: int,
                   loaded_at: datetime | None) -> MaintenanceResult:
        self.changes.append(("reactivate", kind, record_id, user_id, loaded_at))
        return self._answer(RECORDS[kind])

    def add_prefix(self, vendor_id: Any, prefix: str, user_id: int) -> MaintenanceResult:
        self.changes.append(("add_prefix", vendor_id, prefix, user_id))
        return self._answer(PREFIX)

    def move_prefix(self, prefix: str, vendor_id: Any, user_id: int, loaded_at: datetime | None) -> MaintenanceResult:
        self.changes.append(("move_prefix", prefix, vendor_id, user_id, loaded_at))
        return self._answer(PREFIX)

    def _answer(self, record: Any) -> MaintenanceResult:
        if self.answer is not None:
            return self.answer
        return MaintenanceResult(MaintenanceStatus.DONE, record, changed_by=SAM.user_id, changed_at=LOADED)


def make_client(reference: FakeReference, user: User = SAM) -> FlaskClient:
    """A test client signed in as `user`, with `reference` as the service."""
    users = FakeUsers(SAM, VIEWER, ADA)
    services = Services(scan_manager=FakeScanManager(), scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                        devices=FakeDevices(), users=users, access=FakeAccess(users), reference=reference)
    client = create_app(services, secret_key=SECRET_KEY).test_client()
    signed_in = client.post("/api/login", json={"username": user.username, "password": PASSWORD})
    assert signed_in.status_code == 200, signed_in.get_json()
    return client


class RouteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = FakeReference()
        self.client = make_client(self.reference)

    def assertNothingChanged(self) -> None:
        self.assertEqual(self.reference.changes, [])


# ---- reading ---------------------------------------------------------------------------

class ListTests(RouteTestCase):
    def test_a_vendor_list(self) -> None:
        response = self.client.get("/api/reference/vendors")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"records": [{"id": 3, "name": "Apple, Inc.", "status": "active"}],
                                               "page": 1, "per_page": 50, "total": 1})
        self.assertEqual(self.reference.lists, [(VENDOR, 1, 50, None, None)])

    def test_a_signature_list_has_type_names(self) -> None:
        self.reference.listing = ReferenceListing([RECORDS[SIGNATURE]], 1, 50, 1, {4: "smart speaker"})
        body = self.client.get("/api/reference/signatures").get_json()
        self.assertEqual(body["records"], [DETAIL_JSON[SIGNATURE]["record"]])

    def test_search_status_and_paging_reach_the_service(self) -> None:
        self.reference.listing = ReferenceListing([RECORDS[DEVICE_TYPE]], 2, 10, 11, {})
        response = self.client.get("/api/reference/device-types?q=%20speaker%20&status=retired&page=2&per_page=10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["records"], [DETAIL_JSON[DEVICE_TYPE]["record"]])
        self.assertEqual(self.reference.lists, [(DEVICE_TYPE, 2, 10, "speaker", "retired")])

    def test_a_blank_search_is_no_search_and_both_is_a_status(self) -> None:
        response = self.client.get("/api/reference/vendors?q=%20%20&status=both")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reference.lists, [(VENDOR, 1, 50, None, "both")])

    def test_bad_values_are_400_and_nothing_is_listed(self) -> None:
        for query in ("page=0", "per_page=201", "per_page=ten", "status=gone", "q=" + "a" * 101):
            with self.subTest(query=query[:20]):
                self.reference.lists.clear()
                response = self.client.get(f"/api/reference/vendors?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                self.assertEqual(self.reference.lists, [])

    def test_an_unknown_kind_is_404(self) -> None:
        response = self.client.get("/api/reference/gadgets")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {
            "error": "There's no reference list called 'gadgets'. Use vendors, device-types, signatures."})
        self.assertEqual(self.reference.lists, [])

    def test_a_viewer_can_list_and_read(self) -> None:
        viewer = make_client(self.reference, VIEWER)
        self.assertEqual(viewer.get("/api/reference/vendors").status_code, 200)
        self.assertEqual(viewer.get("/api/reference/vendors/3").status_code, 200)


class GetOneTests(RouteTestCase):
    def test_each_kind_with_its_usage_and_loaded_at(self) -> None:
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                response = self.client.get(f"/api/reference/{URL[kind]}/{ID[kind]}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json(), DETAIL_JSON[kind])

    def test_only_vendors_have_prefixes(self) -> None:
        for kind in (DEVICE_TYPE, SIGNATURE):
            self.assertNotIn("prefixes", self.client.get(f"/api/reference/{URL[kind]}/{ID[kind]}").get_json())

    def test_a_missing_record_is_404(self) -> None:
        response = self.client.get("/api/reference/vendors/999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {
            "error": "There is no vendor with id 999. It may have been removed; reload the list."})

    def test_an_unknown_kind_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/reference/gadgets/3").status_code, 404)


# ---- changing a record -----------------------------------------------------------------

class AddTests(RouteTestCase):
    def test_each_kind_is_added_with_the_services_field_names(self) -> None:
        for kind in ReferenceKind:
            with self.subTest(kind=kind.value):
                self.setUp()
                response = self.client.post(f"/api/reference/{URL[kind]}", json=ADD_BODY[kind])
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.get_json(), DETAIL_JSON[kind])      # read back, same shape as GET
                self.assertEqual(self.reference.changes, [("add", kind, SERVICE_VALUES[kind], SAM.user_id)])

    def test_an_unknown_field_is_400_naming_the_allowed_ones(self) -> None:
        response = self.client.post("/api/reference/vendors", json={"name": "Acme", "vendor_name": "Acme"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "The vendor can't be saved.",
                                               "reasons": ["'vendor_name' can't be set on a vendor. Send: name."]})
        self.assertNothingChanged()

    def test_a_body_that_isnt_an_object_is_400(self) -> None:
        for body in (["Acme"], "Acme", 5):
            with self.subTest(body=body):
                response = self.client.post("/api/reference/vendors", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), {"error": "Send the values as a JSON object."})
        self.assertNothingChanged()

    def test_an_unknown_kind_is_404_and_nothing_is_added(self) -> None:
        self.assertEqual(self.client.post("/api/reference/gadgets", json={"name": "x"}).status_code, 404)
        self.assertNothingChanged()


class EditTests(RouteTestCase):
    def test_an_edit_sends_the_values_and_the_parsed_loaded_at(self) -> None:
        response = self.client.put("/api/reference/vendors/3", json={"name": "Apple", "loaded_at": LOADED_TEXT})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), DETAIL_JSON[VENDOR])
        self.assertEqual(self.reference.changes, [("edit", VENDOR, 3, {"vendor_name": "Apple"}, SAM.user_id, LOADED)])

    def test_a_signature_edit_can_clear_a_field(self) -> None:
        self.client.put("/api/reference/signatures/9", json={"resulting_model": None, "loaded_at": LOADED_TEXT})
        self.assertEqual(self.reference.changes[0][3], {"resulting_model": None})

    def test_a_missing_or_unreadable_loaded_at_is_400(self) -> None:
        for loaded_at in (None, "yesterday", 5, "2026-11-11T10:15:02Z", "2026-11-11T10:15:02+00:00"):
            with self.subTest(loaded_at=loaded_at):
                body = {"name": "Apple"} if loaded_at is None else {"name": "Apple", "loaded_at": loaded_at}
                response = self.client.put("/api/reference/vendors/3", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), {
                    "error": "loaded_at is missing or unreadable. Reload the record and try again."})
        self.assertNothingChanged()

    def test_nothing_to_change_is_400(self) -> None:
        response = self.client.put("/api/reference/device-types/4", json={"loaded_at": LOADED_TEXT})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["reasons"], ["Nothing to change. Send at least one of: name."])
        self.assertNothingChanged()


class RetireAndReactivateTests(RouteTestCase):
    def test_each_sends_loaded_at_and_replies_with_the_record(self) -> None:
        for action in ("retire", "reactivate"):
            for kind in ReferenceKind:
                with self.subTest(action=action, kind=kind.value):
                    self.setUp()
                    response = self.client.post(f"/api/reference/{URL[kind]}/{ID[kind]}/{action}",
                                                json={"loaded_at": LOADED_TEXT})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.get_json(), DETAIL_JSON[kind])
                    self.assertEqual(self.reference.changes, [(action, kind, ID[kind], SAM.user_id, LOADED)])

    def test_without_loaded_at_is_400(self) -> None:
        for action in ("retire", "reactivate"):
            self.assertEqual(self.client.post(f"/api/reference/vendors/3/{action}", json={}).status_code, 400)
        self.assertNothingChanged()


# ---- OUI prefixes ----------------------------------------------------------------------

class PrefixTests(RouteTestCase):
    def test_add_a_prefix(self) -> None:
        response = self.client.post("/api/reference/vendors/3/prefixes", json={"prefix": "f0-d1-a9"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json(), {"prefix": "F0:D1:A9", "vendor_id": 3, "loaded_at": LOADED_TEXT})
        self.assertEqual(self.reference.changes, [("add_prefix", 3, "f0-d1-a9", SAM.user_id)])

    def test_add_without_a_prefix_is_400(self) -> None:
        for body in ({}, {"prefix": 286}):
            self.assertEqual(self.client.post("/api/reference/vendors/3/prefixes", json=body).status_code, 400)
        self.assertNothingChanged()

    def test_move_a_prefix(self) -> None:
        response = self.client.put("/api/reference/prefixes/F0:D1:A9", json={"vendor_id": 12, "loaded_at": LOADED_TEXT})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"prefix": "F0:D1:A9", "vendor_id": 3, "loaded_at": LOADED_TEXT})
        self.assertEqual(self.reference.changes, [("move_prefix", "F0:D1:A9", 12, SAM.user_id, LOADED)])

    def test_an_all_digit_prefix_reaches_the_move_route(self) -> None:
        self.client.put("/api/reference/prefixes/123456", json={"vendor_id": 12, "loaded_at": LOADED_TEXT})
        self.assertEqual(self.reference.changes[0][:2], ("move_prefix", "123456"))

    def test_move_needs_a_vendor_and_loaded_at(self) -> None:
        for body in ({"loaded_at": LOADED_TEXT}, {"vendor_id": 12}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put("/api/reference/prefixes/F0:D1:A9", json=body).status_code, 400)
        self.assertNothingChanged()


# ---- refusals --------------------------------------------------------------------------

class RefusalTests(RouteTestCase):
    def refuse(self, result: MaintenanceResult, kind: ReferenceKind = VENDOR, loaded_at: str = LOADED_TEXT) -> Any:
        self.reference.answer = result
        return self.client.put(f"/api/reference/{URL[kind]}/{ID[kind]}",
                               json={**ADD_BODY[kind], "loaded_at": loaded_at})

    def test_each_refusal_for_each_kind(self) -> None:
        for kind in ReferenceKind:
            label = {VENDOR: "vendor", DEVICE_TYPE: "device type", SIGNATURE: "signature"}[kind]
            cases = (
                (MaintenanceResult(MaintenanceStatus.REJECTED, reasons=("Too short.", "Unknown type.")), 400,
                 {"error": f"The {label} can't be saved.", "reasons": ["Too short.", "Unknown type."]}),
                (MaintenanceResult(MaintenanceStatus.PROTECTED, reasons=("Built in.",)), 403, {"error": "Built in."}),
                (MaintenanceResult(MaintenanceStatus.NOT_FOUND, reasons=("Gone.",)), 404, {"error": "Gone."}),
                (MaintenanceResult(MaintenanceStatus.DUPLICATE, reasons=("Exists.",), existing_id=88), 409,
                 {"error": "Exists.", "existing_id": 88}),
                (MaintenanceResult(MaintenanceStatus.CONFLICT, reasons=("x",), changed_by=ADA.user_id,
                                   changed_at=CHANGED), 409,
                 {"error": f"This {label} was changed by ada at 14:05 after you opened it. "
                           "Reload to see their changes.", "changed_by": "ada", "changed_at": "2026-11-11T14:05:31"}),
            )
            for result, status, body in cases:
                with self.subTest(kind=kind.value, status=result.status.value):
                    response = self.refuse(result, kind)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.get_json(), body)

    def test_a_conflict_on_another_day_shows_the_date(self) -> None:
        result = MaintenanceResult(MaintenanceStatus.CONFLICT, changed_by=ADA.user_id, changed_at=CHANGED)
        error = self.refuse(result, loaded_at="2026-11-10T09:00:00").get_json()["error"]
        self.assertIn("changed by ada at 2026-11-11 14:05 after you opened it", error)

    def test_a_conflict_by_an_account_that_is_gone(self) -> None:
        result = MaintenanceResult(MaintenanceStatus.CONFLICT, changed_by=99, changed_at=CHANGED)
        body = self.refuse(result).get_json()
        self.assertEqual(body["changed_by"], "another user")
        self.assertIn("changed by another user at 14:05", body["error"])

    def test_retire_refusals(self) -> None:
        for status, code in ((MaintenanceStatus.PROTECTED, 403), (MaintenanceStatus.NOT_FOUND, 404)):
            with self.subTest(status=status.value):
                self.reference.answer = MaintenanceResult(status, reasons=("No.",))
                response = self.client.post("/api/reference/device-types/1/retire", json={"loaded_at": LOADED_TEXT})
                self.assertEqual((response.status_code, response.get_json()), (code, {"error": "No."}))

    def test_a_prefix_refusal_says_oui_prefix(self) -> None:
        self.reference.answer = MaintenanceResult(MaintenanceStatus.REJECTED, reasons=("'xyz' is not an OUI prefix.",))
        response = self.client.post("/api/reference/vendors/3/prefixes", json={"prefix": "xyz"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "The OUI prefix can't be saved.")


# ---- who may change --------------------------------------------------------------------

CHANGE_ROUTES = (
    ("POST", "/api/reference/vendors", {"name": "Acme"}),
    ("PUT", "/api/reference/vendors/3", {"name": "Acme", "loaded_at": LOADED_TEXT}),
    ("POST", "/api/reference/vendors/3/retire", {"loaded_at": LOADED_TEXT}),
    ("POST", "/api/reference/vendors/3/reactivate", {"loaded_at": LOADED_TEXT}),
    ("POST", "/api/reference/vendors/3/prefixes", {"prefix": "F0:D1:A9"}),
    ("PUT", "/api/reference/prefixes/F0:D1:A9", {"vendor_id": 3, "loaded_at": LOADED_TEXT}),
)


class ViewerTests(unittest.TestCase):
    def test_a_viewer_gets_403_on_every_change_route_logged_and_nothing_called(self) -> None:
        reference = FakeReference()
        client = make_client(reference, VIEWER)
        for method, url, body in CHANGE_ROUTES:
            with self.subTest(route=f"{method} {url}"):
                with self.assertLogs(AUTH_LOG, level="WARNING"):                   # AT-13
                    response = client.open(url, method=method, json=body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json(), {"error": "Your role (viewer) can't edit reference data."})
        self.assertEqual(reference.changes, [])

    def test_an_admin_can_change(self) -> None:
        response = make_client(FakeReference(), ADA).post("/api/reference/vendors", json={"name": "Acme"})
        self.assertEqual(response.status_code, 201)


if __name__ == "__main__":
    unittest.main()
