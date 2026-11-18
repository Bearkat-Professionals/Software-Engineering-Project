"""Controller tests for account administration and personal password changes (#92).

No database and no Argon2: service rules have their own unit tests. These tests
cover routing, permissions, response safety and session behavior.
"""
from __future__ import annotations

import unittest

from backend.records.enums import AccountStatus, UserRole
from tests.controller.test_auth import ADA, SAM, VAL, AuthTestCase
from tests.fakes.fake_accounts import PASSWORD


class UserRouteTests(AuthTestCase):
    def sign_in_as_admin(self) -> None:
        self.assertEqual(self.sign_in(ADA).status_code, 200)

    def test_admin_lists_accounts_without_password_hashes(self) -> None:
        self.sign_in_as_admin()
        response = self.client.get("/api/users")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([user["username"] for user in response.get_json()["users"]],
                         ["sam", "val", "ada"])
        self.assertNotIn("password", response.get_data(as_text=True))
        self.assertNotIn("argon", response.get_data(as_text=True).lower())

    def test_admin_adds_a_viewer_who_cannot_manage_accounts(self) -> None:
        self.sign_in_as_admin()
        response = self.client.post("/api/users", json={
            "username": "new.viewer",
            "password": "a long enough password",
            "role": "viewer",
        })
        self.assertEqual(response.status_code, 201)
        created = response.get_json()["user"]
        self.assertEqual((created["username"], created["role"]), ("new.viewer", "viewer"))
        self.assertNotIn("password_hash", created)
        self.assertEqual(self.app.extensions["katscan_access"].users.find(created["user_id"]).role,
                         UserRole.VIEWER)

        viewer = self.app.test_client()
        self.assertEqual(viewer.post("/api/login", json={
            "username": "new.viewer", "password": PASSWORD}).status_code, 200)
        with self.assertLogs("backend.controller.auth", level="WARNING"):
            self.assertEqual(viewer.get("/api/users").status_code, 403)

    def test_admin_changes_role_status_and_password_as_themselves(self) -> None:
        self.sign_in_as_admin()
        calls = (
            (f"/api/users/{SAM.user_id}/role", {"role": "viewer"}),
            (f"/api/users/{SAM.user_id}/status", {"status": "disabled"}),
            (f"/api/users/{SAM.user_id}/password", {"password": "a brand new password"}),
        )
        for path, body in calls:
            with self.subTest(path=path):
                response = self.client.put(path, json=body)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertNotIn("password_hash", response.get_data(as_text=True))

        self.assertEqual(self.users.find(SAM.user_id).role, UserRole.VIEWER)
        self.assertEqual(self.users.find(SAM.user_id).account_status, AccountStatus.DISABLED)

    def test_editor_gets_403_on_every_user_admin_route(self) -> None:
        self.sign_in(SAM)
        routes = (
            ("GET", "/api/users", None),
            ("POST", "/api/users", {"username": "x", "password": PASSWORD, "role": "viewer"}),
            ("PUT", f"/api/users/{VAL.user_id}/role", {"role": "editor"}),
            ("PUT", f"/api/users/{VAL.user_id}/status", {"status": "disabled"}),
            ("PUT", f"/api/users/{VAL.user_id}/password", {"password": PASSWORD}),
        )
        for method, path, body in routes:
            with self.subTest(route=f"{method} {path}"), \
                    self.assertLogs("backend.controller.auth", level="WARNING"):
                response = self.client.open(path, method=method, json=body)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.app.extensions["katscan_access"].users.find(VAL.user_id), VAL)

    def test_disabling_a_user_ends_their_session_on_the_next_request(self) -> None:
        viewer = self.app.test_client()
        self.assertEqual(self.sign_in(VAL, viewer).status_code, 200)
        self.sign_in_as_admin()
        self.assertEqual(self.client.put(
            f"/api/users/{VAL.user_id}/status", json={"status": "disabled"}).status_code, 200)
        with self.assertLogs("backend.controller.auth", level="INFO"):
            response = viewer.get("/api/me")
        self.assertEqual(response.status_code, 401)
        self.assertIn("disabled", response.get_json()["error"].lower())

    def test_any_signed_in_user_can_change_their_own_password(self) -> None:
        self.sign_in(VAL)
        response = self.client.put("/api/me/password", json={
            "current_password": PASSWORD,
            "new_password": "a brand new password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["user_id"], VAL.user_id)
        self.assertNotIn("password", response.get_data(as_text=True))

    def test_missing_account_is_404(self) -> None:
        self.sign_in_as_admin()
        response = self.client.put("/api/users/999999/role", json={"role": "viewer"})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
