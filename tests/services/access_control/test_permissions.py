"""Unit tests for roles and permissions (#88, 2.1). No database.

    python -m unittest tests.services.access_control.test_permissions -v
"""
import ast
import unittest
from pathlib import Path
from unittest import mock

from backend.records import User
from backend.records.enums import AccountStatus, UserRole
from backend.services.access_control import permissions
from backend.services.access_control.permissions import (ACTION_PHRASES, ROLE_PERMISSIONS, AccessDecision,
                                                         Permission, authorize, permissions_for)

# The table agreed on #86, in full: changing a permission must be a visible change here too
AGREED_TABLE = {
    UserRole.VIEWER: {Permission.VIEW_DATA, Permission.EXPORT},
    UserRole.EDITOR: {Permission.VIEW_DATA, Permission.EXPORT, Permission.START_SCAN, Permission.EDIT_DEVICES,
                      Permission.EDIT_REFERENCE_DATA},
    UserRole.ADMIN: {Permission.VIEW_DATA, Permission.EXPORT, Permission.START_SCAN, Permission.EDIT_DEVICES,
                     Permission.EDIT_REFERENCE_DATA, Permission.MANAGE_ALERTS, Permission.BACKUP_RESTORE,
                     Permission.MANAGE_USERS, Permission.VIEW_AUDIT_LOG},
}


def user(role: UserRole, status: AccountStatus = AccountStatus.ACTIVE) -> User:
    return User(username="someone", password_hash="not-a-real-hash", role=role, account_status=status)


class RolePermissionTableTests(unittest.TestCase):
    def test_every_role_has_a_row(self):
        self.assertEqual(set(ROLE_PERMISSIONS), set(UserRole))

    def test_table_is_exactly_the_agreed_one(self):
        self.assertEqual(ROLE_PERMISSIONS, AGREED_TABLE)
        self.assertEqual(AGREED_TABLE[UserRole.ADMIN], set(Permission))     # a new Permission must be added above

    def test_every_permission_has_an_action_phrase(self):
        self.assertEqual(set(ACTION_PHRASES), set(Permission))
        for phrase in ACTION_PHRASES.values():
            self.assertTrue(phrase.strip())


class AuthorizeTests(unittest.TestCase):
    def test_viewer(self):
        self.assertTrue(authorize(user(UserRole.VIEWER), Permission.VIEW_DATA).allowed)
        self.assertEqual(authorize(user(UserRole.VIEWER), Permission.START_SCAN),
                         AccessDecision(False, "Your role (viewer) can't start scans."))

    def test_editor(self):
        editor = user(UserRole.EDITOR)
        self.assertTrue(authorize(editor, Permission.EDIT_DEVICES).allowed)
        self.assertFalse(authorize(editor, Permission.MANAGE_USERS).allowed)
        self.assertFalse(authorize(editor, Permission.BACKUP_RESTORE).allowed)

    def test_admin_may_do_everything(self):
        for permission in Permission:
            with self.subTest(permission=permission):
                self.assertEqual(authorize(user(UserRole.ADMIN), permission), AccessDecision(True, None))

    def test_disabled_admin_may_do_nothing(self):
        disabled = user(UserRole.ADMIN, AccountStatus.DISABLED)
        for permission in Permission:
            with self.subTest(permission=permission):
                self.assertEqual(authorize(disabled, permission),
                                 AccessDecision(False, "This account is disabled."))

    def test_no_user_must_sign_in(self):
        for permission in Permission:
            with self.subTest(permission=permission):
                self.assertEqual(authorize(None, permission), AccessDecision(False, "Sign in to continue."))

    def test_refusals_have_a_reason_and_allowed_decisions_have_none(self):
        for role in UserRole:
            for permission in Permission:
                with self.subTest(role=role, permission=permission):
                    decision = authorize(user(role), permission)
                    if decision.allowed:
                        self.assertIsNone(decision.reason)
                    else:
                        self.assertTrue(decision.reason)

    def test_role_missing_from_the_table_gets_nothing(self):
        without_viewer = {role: row for role, row in ROLE_PERMISSIONS.items() if role is not UserRole.VIEWER}
        with mock.patch.object(permissions, "ROLE_PERMISSIONS", without_viewer):
            self.assertFalse(authorize(user(UserRole.VIEWER), Permission.VIEW_DATA).allowed)
            self.assertEqual(permissions_for(user(UserRole.VIEWER)), [])


class PermissionsForTests(unittest.TestCase):
    def test_returns_sorted_names(self):
        self.assertEqual(permissions_for(user(UserRole.VIEWER)), ["EXPORT", "VIEW_DATA"])
        self.assertEqual(permissions_for(user(UserRole.EDITOR)),
                         ["EDIT_DEVICES", "EDIT_REFERENCE_DATA", "EXPORT", "START_SCAN", "VIEW_DATA"])

    def test_disabled_user_gets_nothing(self):
        self.assertEqual(permissions_for(user(UserRole.ADMIN, AccountStatus.DISABLED)), [])


class HasRoleTests(unittest.TestCase):
    def test_true_only_for_the_exact_role(self):
        for actual in UserRole:
            for asked in UserRole:
                with self.subTest(actual=actual, asked=asked):
                    self.assertEqual(user(actual).has_role(asked), actual is asked)


class PurityTests(unittest.TestCase):
    def test_imports_no_flask_database_or_network_modules(self):
        # Allow-list, so any new import has to be looked at here
        allowed = {"__future__", "dataclasses", "enum", "backend.records", "backend.records.enums"}
        tree = ast.parse(Path(permissions.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, allowed)


if __name__ == "__main__":
    unittest.main()
