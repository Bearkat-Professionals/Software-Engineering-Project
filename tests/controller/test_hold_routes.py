"""Tests for the edit hold routes and the 409 they lead to (SH-2, #141), through Flask's
test client with fake services and a real EditLockGuard on fake clocks. No database.

    py -3.13 -m unittest tests.controller.test_hold_routes -v
"""
from __future__ import annotations

import unittest
from datetime import datetime

from flask import Flask
from flask.testing import FlaskClient

from backend.controller.app_controller import Services, create_app
from backend.controller.route_helpers import being_edited_json
from backend.records import User
from backend.services.records.device_maintenance import DeviceMaintenanceResult, DeviceMaintenanceStatus
from backend.services.records.reference_maintenance import MaintenanceResult, MaintenanceStatus, ReferenceKind
from backend.services.scan.scan_session import ScanState, ScanStatusReport
from backend.shared_modules.edit_lock_guard import EditLockGuard
from tests.controller.test_api_routes import FakeDevices, FakeScanManager, FakeSessions
from tests.controller.test_device_routes import ADA, SAM, VIEWER, FakeDeviceMaintenance
from tests.controller.test_reference_routes import ID, FakeReference
from tests.fakes.fake_accounts import PASSWORD, SECRET_KEY, FakeAccess, FakeUsers

AUTH_LOG = "backend.controller.auth"
STARTED = datetime(2026, 11, 13, 14, 5, 12)
STARTED_TEXT = "2026-11-13T14:05:12"
DEVICE_7 = {"record_type": "device", "record_id": 7}
VENDOR_3 = {"record_type": "vendor", "record_id": ID[ReferenceKind.VENDOR]}


class HoldTestCase(unittest.TestCase):
    """One app, shared by several signed-in users (one test client each), like real browsers."""

    def setUp(self) -> None:
        self.now = 1000.0
        self.holds = EditLockGuard(clock=lambda: self.now, wall_clock=lambda: STARTED)
        self.maintenance = FakeDeviceMaintenance()
        self.reference = FakeReference()
        users = FakeUsers(SAM, VIEWER, ADA)
        services = Services(scan_manager=FakeScanManager(),
                            scan_sessions=FakeSessions(ScanStatusReport(state=ScanState.NONE)),
                            devices=FakeDevices(), users=users, access=FakeAccess(users),
                            reference=self.reference, device_maintenance=self.maintenance,
                            edit_holds=self.holds)
        self.app: Flask = create_app(services, secret_key=SECRET_KEY)
        self.sam = self.sign_in(SAM)

    def sign_in(self, user: User) -> FlaskClient:
        client = self.app.test_client()
        response = client.post("/api/login", json={"username": user.username, "password": PASSWORD})
        assert response.status_code == 200, response.get_json()
        return client


# ---- taking a hold -------------------------------------------------------------------------

class TakeTests(HoldTestCase):

    def test_take_a_hold_on_a_device(self) -> None:
        response = self.sam.post("/api/holds", json=DEVICE_7)
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(set(body), {"hold_id", "expires_in_s", "others"})
        self.assertEqual((body["expires_in_s"], body["others"]), (120, []))
        self.assertEqual(self.holds.held_by_others(("device", 7), user_id=99)[0].user_id, SAM.user_id)

    def test_every_reference_kind_can_be_held(self) -> None:
        for kind, record_type in ((ReferenceKind.VENDOR, "vendor"), (ReferenceKind.DEVICE_TYPE, "device_type"),
                                  (ReferenceKind.SIGNATURE, "device_signature")):
            with self.subTest(kind=kind):
                response = self.sam.post("/api/holds", json={"record_type": record_type, "record_id": ID[kind]})
                self.assertEqual(response.status_code, 201, response.get_json())

    def test_the_reply_names_everyone_else_editing(self) -> None:
        self.sign_in(ADA).post("/api/holds", json=DEVICE_7)
        body = self.sam.post("/api/holds", json=DEVICE_7).get_json()
        self.assertEqual(body["others"], [{"username": "ada", "since": STARTED_TEXT}])

    def test_bad_record_type_or_id_is_400(self) -> None:
        cases = [({"record_type": "scan", "record_id": 7}, "record_type must be one of"),
                 ({"record_id": 7}, "record_type must be one of"),
                 ({"record_type": "device", "record_id": "7"}, "record_id must be a whole number"),
                 ({"record_type": "device", "record_id": True}, "record_id must be a whole number"),
                 ({"record_type": "device", "record_id": 0}, "record_id must be a whole number"),
                 ({"record_type": "device"}, "record_id must be a whole number")]
        for body, error in cases:
            with self.subTest(body=body):
                response = self.sam.post("/api/holds", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(error, response.get_json()["error"])
        self.assertEqual(self.holds.count(), 0)

    def test_a_body_that_isnt_an_object_is_400_and_not_json_is_415(self) -> None:
        self.assertEqual(self.sam.post("/api/holds", json=[1, 2]).status_code, 400)
        self.assertEqual(self.sam.post("/api/holds", data="record_type=device",
                                       content_type="application/x-www-form-urlencoded").status_code, 415)

    def test_an_unknown_record_is_404(self) -> None:
        for body, error in (({"record_type": "device", "record_id": 99}, "There is no device with id 99"),
                            ({"record_type": "vendor", "record_id": 99}, "There is no vendor with id 99")):
            with self.subTest(body=body):
                response = self.sam.post("/api/holds", json=body)
                self.assertEqual(response.status_code, 404)
                self.assertIn(error, response.get_json()["error"])
        self.assertEqual(self.holds.count(), 0)

    def test_a_viewer_cant_take_a_hold_and_it_is_logged(self) -> None:
        viewer = self.sign_in(VIEWER)
        for body in (DEVICE_7, VENDOR_3):
            with self.subTest(body=body):
                with self.assertLogs(AUTH_LOG, "WARNING") as logs:
                    response = viewer.post("/api/holds", json=body)
                self.assertEqual(response.status_code, 403)
                self.assertIn("val", logs.output[0])
        self.assertEqual(self.holds.count(), 0)

    def test_signed_out_is_401(self) -> None:
        self.assertEqual(self.app.test_client().post("/api/holds", json=DEVICE_7).status_code, 401)


# ---- renewing and releasing ----------------------------------------------------------------

class RenewReleaseTests(HoldTestCase):

    def take(self, client: FlaskClient, body: dict = DEVICE_7) -> str:
        return client.post("/api/holds", json=body).get_json()["hold_id"]

    def test_renew_answers_the_same_shape(self) -> None:
        hold_id = self.take(self.sam)
        self.sign_in(ADA).post("/api/holds", json=DEVICE_7)
        self.now += 100
        response = self.sam.put(f"/api/holds/{hold_id}", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"hold_id": hold_id, "expires_in_s": 120,
                                               "others": [{"username": "ada", "since": STARTED_TEXT}]})
        self.now += 100                                     # 200 s after taking it: alive thanks to the renew
        self.assertEqual(self.holds.count(), 1)             # ada's (taken at 1000) has expired; sam's lives

    def test_renewing_an_ended_hold_is_404(self) -> None:
        hold_id = self.take(self.sam)
        self.now += 120
        for path in (f"/api/holds/{hold_id}", "/api/holds/never-taken"):
            with self.subTest(path=path):
                response = self.sam.put(path, json={})
                self.assertEqual(response.status_code, 404)
                self.assertIn("Take a new one", response.get_json()["error"])

    def test_someone_elses_hold_cant_be_renewed_or_released(self) -> None:
        hold_id = self.take(self.sam)
        ada = self.sign_in(ADA)
        self.assertEqual(ada.put(f"/api/holds/{hold_id}", json={}).status_code, 404)
        self.assertEqual(ada.delete(f"/api/holds/{hold_id}", json={}).status_code, 204)    # says nothing
        self.assertEqual(self.holds.count(), 1)                                             # but it's still held

    def test_release(self) -> None:
        hold_id = self.take(self.sam)
        self.assertEqual(self.sam.delete(f"/api/holds/{hold_id}", json={}).status_code, 204)
        self.assertEqual(self.holds.count(), 0)
        self.assertEqual(self.sam.delete(f"/api/holds/{hold_id}", json={}).status_code, 204)  # again: fine
        self.assertEqual(self.sam.put(f"/api/holds/{hold_id}", json={}).status_code, 404)

    def test_renew_and_release_need_json_too(self) -> None:
        hold_id = self.take(self.sam)
        self.assertEqual(self.sam.put(f"/api/holds/{hold_id}").status_code, 415)
        self.assertEqual(self.sam.delete(f"/api/holds/{hold_id}").status_code, 415)

    def test_signing_out_releases_only_that_sessions_holds(self) -> None:
        self.take(self.sam)
        self.take(self.sam, VENDOR_3)
        other_browser = self.sign_in(SAM)                   # sam, signed in a second time
        self.take(other_browser)
        self.assertEqual(self.holds.count(), 3)
        self.assertEqual(self.sam.post("/api/logout", json={}).status_code, 204)
        self.assertEqual(self.holds.count(), 1)             # the other browser's hold stays

    def test_signing_in_again_in_the_same_browser_releases_the_old_sessions_holds(self) -> None:
        self.take(self.sam)
        self.sam.post("/api/login", json={"username": SAM.username, "password": PASSWORD})
        self.assertEqual(self.holds.count(), 0)


# ---- the refusals a hold leads to ------------------------------------------------------------

class BeingEditedTests(HoldTestCase):

    def test_a_refused_delete_is_409_naming_who(self) -> None:
        self.maintenance.answer = DeviceMaintenanceResult(
            DeviceMaintenanceStatus.BEING_EDITED, held_by=(ADA.user_id,), held_since=STARTED,
            reasons=("Someone else is editing this device. Try again when they've finished.",))
        response = self.sam.delete("/api/devices/7?loaded_at=2026-11-13T14:00:00", json={})
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertEqual((body["held_by"], body["since"]), (["ada"], STARTED_TEXT))
        self.assertIn("ada is editing this device (since", body["error"])
        self.assertTrue(body["error"].endswith("Try again when they've finished."))

    def test_a_refused_retire_is_409_naming_who(self) -> None:
        self.reference.answer = MaintenanceResult(
            MaintenanceStatus.BEING_EDITED, held_by=(ADA.user_id, SAM.user_id), held_since=STARTED,
            reasons=("Someone else is editing this vendor. Try again when they've finished.",))
        response = self.sam.post(f"/api/reference/vendors/{ID[ReferenceKind.VENDOR]}/retire",
                                 json={"loaded_at": "2026-11-13T14:00:00"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["held_by"], ["ada", "sam"])
        self.assertIn("ada and sam are editing this vendor", response.get_json()["error"])


class BeingEditedWordingTests(unittest.TestCase):
    """route_helpers.being_edited_json, on its own."""

    USERS = FakeUsers(SAM, ADA, VIEWER)

    def test_today_shows_the_time_another_day_the_date(self) -> None:
        today = being_edited_json(self.USERS, "device", (6,), STARTED, today=lambda: STARTED.date())
        self.assertEqual(today, {"error": "ada is editing this device (since 14:05). Try again when they've finished.",
                                 "held_by": ["ada"], "since": STARTED_TEXT})
        later = being_edited_json(self.USERS, "device", (6,), STARTED, today=lambda: datetime(2026, 11, 14).date())
        self.assertIn("(since 2026-11-13 14:05)", later["error"])

    def test_three_names_and_an_unknown_user(self) -> None:
        body = being_edited_json(self.USERS, "vendor", (4, 6, 999), None)
        self.assertEqual(body["error"], "sam, ada and another user are editing this vendor. Try again when they've finished.")
        self.assertIsNone(body["since"])


if __name__ == "__main__":
    unittest.main()
