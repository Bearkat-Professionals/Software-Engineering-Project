"""Integration tests for accounts and settings (#17, section 3.3).

Same TEST database setup as test_repository.py; skipped if it is not available.

    python -m unittest tests.data_access.test_account_settings -v
"""
import unittest
from dataclasses import replace

from backend.data_access.account_settings import (AlertSettingStore, UnknownAlertEventError,
                                                  UserStore)
from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import IntegrityConstraintError
from backend.data_access.repository import RecordNotFoundError
from backend.records import AlertSetting, User
from backend.records.enums import AlertEventName, AuditAction, UserRole
# Only the helpers are imported, not RepositoryTests, so those tests don't run twice
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

HASH = "$2b$12$not.a.real.hash.just.for.tests........................"


class AccountSettingsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()
        cls.users = UserStore(cls.engine)
        cls.alerts = AlertSettingStore(cls.engine)
        cls.audit = AuditTrail(cls.engine)

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)


# ---- 3.3.1 and 3.3.2 users ---------------------------------------------------

class UserStoreTests(AccountSettingsTestCase):
    def test_new_user_can_be_found_by_id_and_username(self):
        user = self.users.store(User(username="sam", password_hash=HASH))
        self.assertIsNotNone(user.user_id)
        self.assertEqual(self.users.find(user.user_id).username, "sam")
        self.assertEqual(self.users.find_by_username("sam").user_id, user.user_id)
        self.assertEqual(self.users.find_by_username("sam").password_hash, HASH)   # kept for login checks
        self.assertIsNone(self.users.find_by_username("nobody"))
        self.assertIsNone(self.users.find(999_999))

    def test_new_user_gets_an_insert_audit_entry(self):
        user = self.users.store(User(username="sam", password_hash=HASH))
        [entry] = self.audit.history("app_user", user.user_id)
        self.assertEqual(entry.action, AuditAction.INSERT)
        self.assertEqual(entry.user_id, user.user_id)            # first account records itself
        self.assertEqual(entry.new_value["username"], "sam")

    def test_password_hash_is_never_in_api_output_or_the_audit_log(self):
        user = self.users.store(User(username="sam", password_hash=HASH))
        self.users.store(replace(self.users.find(user.user_id), role=UserRole.EDITOR))
        self.assertNotIn("password_hash", self.users.find(user.user_id).to_dict())
        for entry in self.audit.history("app_user", user.user_id):
            for snapshot in (entry.old_value, entry.new_value):
                self.assertNotIn("password_hash", snapshot or {})
                self.assertNotIn(HASH, str(snapshot))

    def test_update_is_audited_with_who_changed_it(self):
        admin = self.users.store(User(username="admin", password_hash=HASH, role=UserRole.ADMIN))
        sam = self.users.store(User(username="sam", password_hash=HASH))
        self.users.store(replace(sam, role=UserRole.EDITOR), changed_by=admin.user_id)

        self.assertEqual(self.users.find(sam.user_id).role, UserRole.EDITOR)
        entry = self.audit.latest("app_user", sam.user_id)
        self.assertEqual(entry.action, AuditAction.UPDATE)
        self.assertEqual(entry.user_id, admin.user_id)
        self.assertEqual((entry.old_value["role"], entry.new_value["role"]), ("viewer", "editor"))

    def test_duplicate_username_saves_nothing(self):
        self.users.store(User(username="sam", password_hash=HASH))
        with self.assertRaises(IntegrityConstraintError):
            self.users.store(User(username="sam", password_hash=HASH))
        with self.engine.unit_of_work() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM audit_log")
            self.assertEqual(cursor.fetchone()["n"], 1)          # only the first user's entry

    def test_updating_a_missing_user_fails_without_an_audit_entry(self):
        with self.assertRaises(RecordNotFoundError):
            self.users.store(User(user_id=999_999, username="ghost", password_hash=HASH))
        self.assertEqual(self.audit.history("app_user", 999_999), [])

    def test_user_without_a_password_hash_is_rejected(self):
        with self.assertRaises(ValueError):
            self.users.store(User(username="sam", password_hash=""))
        self.assertIsNone(self.users.find_by_username("sam"))


# ---- 3.3.3 and 3.3.4 alert settings ------------------------------------------

class AlertSettingStoreTests(AccountSettingsTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.users.store(User(username="owner", password_hash=HASH))

    def _new_setting(self, *events, email="owner@example.com") -> AlertSetting:
        return AlertSetting(user_id=self.owner.user_id, recipient_email=email, events=list(events))

    def test_setting_with_two_events_loads_with_the_same_two(self):        # Done when
        saved = self.alerts.store(self._new_setting(AlertEventName.SCAN_FAILED,
                                                    AlertEventName.FLAGGED_DEVICE))
        loaded = self.alerts.find(saved.alert_setting_id)
        self.assertEqual(set(loaded.events), {AlertEventName.SCAN_FAILED, AlertEventName.FLAGGED_DEVICE})
        self.assertEqual(loaded.recipient_email, "owner@example.com")
        self.assertTrue(loaded.enabled)

    def test_storing_again_replaces_the_events(self):
        saved = self.alerts.store(self._new_setting(AlertEventName.SCAN_FAILED,
                                                    AlertEventName.FLAGGED_DEVICE))
        saved.events = [AlertEventName.BACKUP_FAILED]
        saved.enabled = False
        self.alerts.store(saved)
        loaded = self.alerts.find(saved.alert_setting_id)
        self.assertEqual(loaded.events, [AlertEventName.BACKUP_FAILED])
        self.assertFalse(loaded.enabled)

        saved.events = []
        self.alerts.store(saved)
        self.assertEqual(self.alerts.find(saved.alert_setting_id).events, [])

    def test_same_event_twice_is_stored_once(self):
        saved = self.alerts.store(self._new_setting(AlertEventName.SCAN_FAILED,
                                                    AlertEventName.SCAN_FAILED))
        self.assertEqual(self.alerts.find(saved.alert_setting_id).events, [AlertEventName.SCAN_FAILED])

    def test_unknown_event_saves_nothing(self):
        with self.assertRaises(UnknownAlertEventError):
            self.alerts.store(self._new_setting(AlertEventName.SCAN_FAILED, "not_an_event"))
        self.assertEqual(self.alerts.for_user(self.owner.user_id), [])

    def test_for_user_returns_only_that_users_settings(self):
        other = self.users.store(User(username="other", password_hash=HASH))
        self.alerts.store(self._new_setting(AlertEventName.SCAN_FAILED))
        self.alerts.store(AlertSetting(user_id=other.user_id, recipient_email="other@example.com",
                                       events=[AlertEventName.BACKUP_FAILED]))
        mine = self.alerts.for_user(self.owner.user_id)
        self.assertEqual([(s.recipient_email, s.events) for s in mine],
                         [("owner@example.com", [AlertEventName.SCAN_FAILED])])

    def test_missing_setting(self):
        self.assertIsNone(self.alerts.find(999_999))


if __name__ == "__main__":
    unittest.main()
