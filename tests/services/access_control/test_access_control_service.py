"""Unit tests for sign in and the account rules (#89). No database: a fake user store
and a fake clock. Real Argon2id hashing, so a run takes a few seconds.

    python -m unittest tests.services.access_control.test_access_control_service -v
"""
from __future__ import annotations

import contextlib
import io
import unittest
from dataclasses import replace
from unittest import mock

from backend.black_boxes.password_hashing import hash_password, verify_password
from backend.data_access.account_settings import FirstUserExistsError, LastActiveAdminError
from backend.data_access.database_engine import IntegrityConstraintError
from backend.data_access.repository import RecordNotFoundError
from backend.records import User
from backend.records.enums import AccountStatus, UserRole
from backend.services.access_control import access_control_service as module
from backend.services.access_control.access_control_service import (
    ACCOUNT_NOT_FOUND, DISABLED_LOGIN, FAILURE_WINDOW_S, LAST_ADMIN, LOCKOUT_S,
    PASSWORD_MAX, PASSWORD_MIN, SELF_ROLE, SELF_STATUS, WRONG_LOGIN, AccessControlService)
from backend.services.access_control.create_admin import create_first_admin

PASSWORD = "correct horse battery"
ADMIN_ID = 1


class FakeUserStore:
    """3.3's UserStore in memory: usernames match ignoring case, like the MySQL column."""
    def __init__(self, *users: User) -> None:
        self.users: dict[int, User] = {user.user_id: user for user in users}
        self.stored: list[tuple[User, int]] = []        # (user, changed_by) for each store()
        self.raise_on_store: Exception | None = None

    def find_by_username(self, username: str) -> User | None:
        return next((user for user in self.users.values()
                     if user.username.casefold() == username.casefold()), None)

    def find(self, user_id: int) -> User | None:
        return self.users.get(user_id)

    def list(self) -> list[User]:
        return [self.users[user_id] for user_id in sorted(self.users)]

    def store(self, user: User, changed_by: int) -> User:
        if self.raise_on_store is not None:
            raise self.raise_on_store
        if user.user_id is None:
            user = replace(user, user_id=max(self.users, default=0) + 1)
        self.users[user.user_id] = user
        self.stored.append((user, changed_by))
        return user

    def apply_account_change(self, user_id: int, changed_by: int, *,
                             role: UserRole | None = None,
                             account_status: AccountStatus | None = None,
                             password_hash: str | None = None) -> User:
        current = self.find(user_id)
        if current is None:
            raise RecordNotFoundError("missing user")
        changed = replace(
            current,
            role=role if role is not None else current.role,
            account_status=(account_status if account_status is not None
                            else current.account_status),
            password_hash=password_hash if password_hash is not None else current.password_hash,
        )
        removes_active_admin = (
            current.role == UserRole.ADMIN
            and current.account_status == AccountStatus.ACTIVE
            and (changed.role != UserRole.ADMIN
                 or changed.account_status != AccountStatus.ACTIVE)
        )
        active_admins = sum(
            user.role == UserRole.ADMIN and user.account_status == AccountStatus.ACTIVE
            for user in self.users.values())
        if removes_active_admin and active_admins <= 1:
            raise LastActiveAdminError("last admin")
        return self.store(changed, changed_by)

    def create_first_user(self, user: User) -> User:
        if self.users:
            raise FirstUserExistsError("users already exist")
        user = replace(user, user_id=1)
        self.users[1] = user
        return user


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_user(username: str = "sam", status: AccountStatus = AccountStatus.ACTIVE, user_id: int = 4) -> User:
    return User(user_id=user_id, username=username, password_hash=hash_password(PASSWORD),
                role=UserRole.EDITOR, account_status=status)


class ServiceTestCase(unittest.TestCase):
    SAM = make_user()               # hashed once for the whole file: hashing is slow on purpose

    def setUp(self) -> None:
        self.store = FakeUserStore(self.SAM)
        self.clock = FakeClock()
        self.service = AccessControlService(self.store, clock=self.clock)


class SignInTests(ServiceTestCase):
    def test_the_right_password_signs_in(self) -> None:
        result = self.service.authenticate("sam", PASSWORD)
        self.assertTrue(result.signed_in)
        self.assertEqual((result.user, result.reason), (self.SAM, None))

    def test_username_case_and_surrounding_spaces_do_not_matter(self) -> None:
        self.assertTrue(self.service.authenticate("  SAM ", PASSWORD).signed_in)

    def test_wrong_password_and_unknown_username_say_the_same_thing(self) -> None:
        wrong = self.service.authenticate("sam", "wrong password!!")
        unknown = self.service.authenticate("nobody", PASSWORD)
        self.assertEqual((wrong.user, wrong.reason), (None, WRONG_LOGIN))
        self.assertEqual((unknown.user, unknown.reason), (None, WRONG_LOGIN))

    def test_an_unknown_username_still_checks_a_password(self) -> None:
        with mock.patch.object(module, "verify_password", wraps=verify_password) as spy:
            self.service.authenticate("nobody", PASSWORD)
        spy.assert_called_once()
        self.assertTrue(spy.call_args.args[0].startswith("$argon2id$"))    # a real hash: real cost

    def test_bad_input_is_a_wrong_login_not_an_error(self) -> None:
        for username, password in (("", PASSWORD), (None, PASSWORD), ("sam", None), ("sam", "")):
            with self.subTest(username=username, password=password):
                self.assertEqual(self.service.authenticate(username, password).reason, WRONG_LOGIN)

    def test_a_disabled_account_is_refused_after_the_right_password(self) -> None:
        self.store.users[9] = make_user("off", AccountStatus.DISABLED, user_id=9)
        self.assertEqual(self.service.authenticate("off", PASSWORD).reason, DISABLED_LOGIN)
        self.assertEqual(self.service.authenticate("off", "wrong password!!").reason, WRONG_LOGIN)


class LockoutTests(ServiceTestCase):
    def fail(self, times: int, username: str = "sam") -> None:
        for _ in range(times):
            self.service.authenticate(username, "wrong password!!")

    def test_the_sixth_attempt_is_refused_even_with_the_right_password(self) -> None:
        self.fail(5)
        with mock.patch.object(module, "verify_password", wraps=verify_password) as spy:
            result = self.service.authenticate("sam", PASSWORD)
        self.assertEqual(result.reason, "Too many attempts. Try again in 15 minutes.")
        spy.assert_not_called()                             # the password isn't even checked

    def test_after_15_minutes_it_works_again(self) -> None:
        self.fail(5)
        self.clock.now += LOCKOUT_S - 61
        self.assertEqual(self.service.authenticate("sam", PASSWORD).reason,
                         "Too many attempts. Try again in 2 minutes.")
        self.clock.now += 61
        self.assertTrue(self.service.authenticate("sam", PASSWORD).signed_in)

    def test_a_success_resets_the_count(self) -> None:
        self.fail(4)
        self.assertTrue(self.service.authenticate("sam", PASSWORD).signed_in)
        self.fail(4)
        self.assertTrue(self.service.authenticate("sam", PASSWORD).signed_in)

    def test_failures_older_than_the_window_do_not_count(self) -> None:
        self.fail(4)
        self.clock.now += FAILURE_WINDOW_S
        self.fail(1)
        self.assertTrue(self.service.authenticate("sam", PASSWORD).signed_in)

    def test_the_lock_is_per_username_and_ignores_case(self) -> None:
        self.fail(5, "SAM")
        self.assertIn("Too many attempts", self.service.authenticate("sam", PASSWORD).reason)
        self.store.users[5] = make_user("kim", user_id=5)
        self.assertTrue(self.service.authenticate("kim", PASSWORD).signed_in)

    def test_unknown_usernames_lock_the_same_way(self) -> None:
        self.fail(5, "nobody")                              # so the lock can't reveal who exists
        self.assertIn("Too many attempts", self.service.authenticate("nobody", PASSWORD).reason)


class WarmUpTests(unittest.TestCase):
    def test_the_dummy_hash_is_made_when_the_service_is_built_not_on_the_first_unknown_username(self) -> None:
        module._dummy_hash.cache_clear()                    # as if the server had just started
        service = AccessControlService(FakeUserStore(ServiceTestCase.SAM))
        with mock.patch.object(module, "hash_password", side_effect=AssertionError("hashed during sign-in")), \
                mock.patch.object(module, "verify_password", wraps=verify_password) as spy:
            result = service.authenticate("nobody", PASSWORD)
        self.assertEqual(result.reason, WRONG_LOGIN)
        self.assertEqual(spy.call_args.args[0], module._dummy_hash())   # checked against the ready hash


class TrackingLimitTests(ServiceTestCase):
    """The failure records are capped, so made-up usernames can't fill memory. Passwords
    aren't really checked here (verify_password is replaced) to keep 1,500 attempts fast."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(module, "verify_password", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fail(self, *usernames: str) -> None:
        for username in usernames:
            self.service.authenticate(username, "wrong password!!")

    def tracked(self) -> list[str]:
        return list(self.service._attempts)

    def test_many_recent_made_up_usernames_stay_within_the_limit(self) -> None:
        with mock.patch.object(module, "MAX_TRACKED", 100):
            self.fail(*(f"fake{n}" for n in range(1500)))
        self.assertEqual(self.tracked(), [f"fake{n}" for n in range(1400, 1500)])   # the newest 100

    def test_expired_usernames_go_before_active_ones(self) -> None:
        with mock.patch.object(module, "MAX_TRACKED", 3):
            self.fail("old")
            self.clock.now += FAILURE_WINDOW_S              # "old" has nothing recent now
            self.fail("a", "b", "c")
        self.assertEqual(self.tracked(), ["a", "b", "c"])

    def test_the_least_recently_failed_goes_first(self) -> None:
        with mock.patch.object(module, "MAX_TRACKED", 3):
            self.fail("a", "b", "c", "a", "d")              # "a" failed again, so "b" is the oldest
        self.assertEqual(self.tracked(), ["c", "a", "d"])

    def test_a_lock_still_holds_below_the_limit(self) -> None:
        with mock.patch.object(module, "MAX_TRACKED", 3):
            self.fail(*["sam"] * 5)
            self.fail("a", "b")
            self.assertIn("Too many attempts", self.service.authenticate("sam", PASSWORD).reason)


class PasswordRuleTests(ServiceTestCase):
    def test_length_edges(self) -> None:
        for length, allowed in ((PASSWORD_MIN - 1, False), (PASSWORD_MIN, True),
                                (PASSWORD_MAX, True), (PASSWORD_MAX + 1, False)):
            with self.subTest(length=length):
                reason = self.service.check_password_rule("sam", "x" * length)
                self.assertEqual(reason is None, allowed, reason)
        self.assertEqual((PASSWORD_MIN, PASSWORD_MAX), (12, 128))

    def test_the_username_as_the_password_is_refused(self) -> None:
        for password in ("averylongusername", "AVERYLONGUSERNAME", " averylongusername "):
            with self.subTest(password=password):
                self.assertIn("same as the username", self.service.check_password_rule("averylongusername", password))

    def test_only_spaces_and_not_text_are_refused(self) -> None:
        self.assertIsNotNone(self.service.check_password_rule("sam", " " * 20))
        self.assertIsNotNone(self.service.check_password_rule("sam", None))

    def test_any_characters_are_allowed(self) -> None:
        self.assertIsNone(self.service.check_password_rule("sam", "pässwörd 🔒 with spaces"))


class CreateUserTests(ServiceTestCase):
    def test_a_new_user_is_stored_hashed_and_audited_with_the_creator(self) -> None:
        result = self.service.create_user("kim", "a long enough password", UserRole.VIEWER, created_by=ADMIN_ID)
        self.assertTrue(result.ok, result.reasons)
        stored, changed_by = self.store.stored[-1]
        self.assertEqual((stored.username, stored.role, changed_by), ("kim", UserRole.VIEWER, ADMIN_ID))
        self.assertTrue(stored.password_hash.startswith("$argon2id$"))
        self.assertNotIn("a long enough password", stored.password_hash)
        self.assertTrue(verify_password(stored.password_hash, "a long enough password"))

    def test_a_taken_username_is_refused_ignoring_case(self) -> None:
        result = self.service.create_user("SAM", "a long enough password", "viewer", created_by=ADMIN_ID)
        self.assertEqual(result.reasons, ("The username 'SAM' is already taken.",))
        self.assertEqual(self.store.stored, [])

    def test_every_problem_is_reported_at_once(self) -> None:
        result = self.service.create_user("bad name!", "short", "boss", created_by=ADMIN_ID)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.reasons), 3)            # username, password, role
        self.assertEqual(self.store.stored, [])

    def test_username_rules(self) -> None:
        for username, allowed in (("kim", True), ("k.im_2-a", True), ("x" * 100, True), ("x" * 101, False),
                                  ("", False), ("two words", False), ("kïm", False), ("kim@home", False)):
            with self.subTest(username=username[:20]):
                self.assertEqual(self.service.check_username(username) is None, allowed)

    def test_a_name_taken_at_the_same_moment_is_refused_not_raised(self) -> None:
        self.store.raise_on_store = IntegrityConstraintError("Duplicate entry 'kim'")
        result = self.service.create_user("kim", "a long enough password", "viewer", created_by=ADMIN_ID)
        self.assertEqual(result.reasons, ("The username 'kim' is already taken.",))


class ChangePasswordTests(ServiceTestCase):
    def test_a_wrong_current_password_is_refused(self) -> None:
        result = self.service.change_password(self.SAM, "wrong password!!", "a brand new password")
        self.assertEqual(result.reasons, ("The current password is incorrect.",))
        self.assertEqual(self.store.stored, [])

    def test_missing_current_password_is_refused_not_raised(self) -> None:
        result = self.service.change_password(self.SAM, None, "a brand new password")
        self.assertEqual(result.reasons, ("The current password is incorrect.",))
        self.assertEqual(self.store.stored, [])

    def test_the_new_password_is_stored_hashed_by_the_user_themselves(self) -> None:
        result = self.service.change_password(self.SAM, PASSWORD, "a brand new password")
        self.assertTrue(result.ok, result.reasons)
        stored, changed_by = self.store.stored[-1]
        self.assertEqual(changed_by, self.SAM.user_id)
        self.assertTrue(verify_password(stored.password_hash, "a brand new password"))
        self.assertFalse(verify_password(stored.password_hash, PASSWORD))

    def test_the_new_password_must_follow_the_rule_and_differ(self) -> None:
        self.assertIn("at least", self.service.change_password(self.SAM, PASSWORD, "short").reasons[0])
        self.assertIn("different", self.service.change_password(self.SAM, PASSWORD, PASSWORD).reasons[0])
        self.assertEqual(self.store.stored, [])


class AdminAccountChangesTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.admin = make_user("ada", user_id=ADMIN_ID)
        self.admin = replace(self.admin, role=UserRole.ADMIN)
        self.store.users[ADMIN_ID] = self.admin

    def test_lists_accounts_in_store_order(self) -> None:
        self.assertEqual([user.user_id for user in self.service.list_users()], [ADMIN_ID, self.SAM.user_id])

    def test_role_and_status_changes_are_stored_with_the_admin(self) -> None:
        role = self.service.set_role(self.SAM.user_id, "viewer", changed_by=ADMIN_ID)
        self.assertTrue(role.ok, role.reasons)
        self.assertEqual((role.user.role, self.store.stored[-1][1]), (UserRole.VIEWER, ADMIN_ID))

        status = self.service.set_status(self.SAM.user_id, "disabled", changed_by=ADMIN_ID)
        self.assertTrue(status.ok, status.reasons)
        self.assertEqual((status.user.account_status, self.store.stored[-1][1]),
                         (AccountStatus.DISABLED, ADMIN_ID))

    def test_an_admin_can_reset_a_password_and_the_hash_is_not_the_plain_text(self) -> None:
        result = self.service.reset_password(
            self.SAM.user_id, "a completely new password", changed_by=ADMIN_ID)
        self.assertTrue(result.ok, result.reasons)
        stored, changed_by = self.store.stored[-1]
        self.assertEqual(changed_by, ADMIN_ID)
        self.assertNotEqual(stored.password_hash, "a completely new password")
        self.assertTrue(verify_password(stored.password_hash, "a completely new password"))

    def test_an_admin_cannot_demote_or_disable_themselves(self) -> None:
        self.assertEqual(
            self.service.set_role(ADMIN_ID, "editor", changed_by=ADMIN_ID).reasons,
            (SELF_ROLE,))
        self.assertEqual(
            self.service.set_status(ADMIN_ID, "disabled", changed_by=ADMIN_ID).reasons,
            (SELF_STATUS,))
        self.assertEqual(self.store.stored, [])

    def test_the_last_active_admin_cannot_be_changed_even_by_another_caller(self) -> None:
        self.assertEqual(
            self.service.set_role(ADMIN_ID, "viewer", changed_by=999).reasons,
            (LAST_ADMIN,))
        self.assertEqual(
            self.service.set_status(ADMIN_ID, "disabled", changed_by=999).reasons,
            (LAST_ADMIN,))

    def test_one_of_two_active_admins_can_be_demoted(self) -> None:
        second = replace(make_user("bea", user_id=2), role=UserRole.ADMIN)
        self.store.users[second.user_id] = second
        result = self.service.set_role(second.user_id, "editor", changed_by=ADMIN_ID)
        self.assertTrue(result.ok, result.reasons)
        self.assertEqual(result.user.role, UserRole.EDITOR)

    def test_bad_values_and_a_missing_account_are_refused(self) -> None:
        self.assertIn("not a role", self.service.set_role(self.SAM.user_id, "boss", ADMIN_ID).reasons[0])
        self.assertIn("not a status", self.service.set_status(self.SAM.user_id, "gone", ADMIN_ID).reasons[0])
        self.assertEqual(self.service.set_role(999, "viewer", ADMIN_ID).reasons, (ACCOUNT_NOT_FOUND,))

    def test_a_no_op_does_not_write_an_audit_entry(self) -> None:
        self.assertTrue(self.service.set_role(self.SAM.user_id, self.SAM.role, ADMIN_ID).ok)
        self.assertTrue(self.service.set_status(
            self.SAM.user_id, self.SAM.account_status, ADMIN_ID).ok)
        self.assertEqual(self.store.stored, [])

class CreateAdminTests(unittest.TestCase):
    def run_prompts(self, store: FakeUserStore, answers: list[str], secrets: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = create_first_admin(store, ask=lambda _: answers.pop(0), ask_secret=lambda _: secrets.pop(0))
        return code, out.getvalue()

    def test_creates_the_first_admin(self) -> None:
        store = FakeUserStore()
        code, _ = self.run_prompts(store, ["admin"], ["a long enough password"] * 2)
        self.assertEqual(code, 0)
        admin = store.users[1]
        self.assertEqual((admin.username, admin.role), ("admin", UserRole.ADMIN))
        self.assertTrue(verify_password(admin.password_hash, "a long enough password"))

    def test_refuses_when_users_exist_and_changes_nothing(self) -> None:
        store = FakeUserStore(ServiceTestCase.SAM)
        code, output = self.run_prompts(store, ["admin"], ["a long enough password"] * 2)
        self.assertEqual(code, 1)
        self.assertIn("already has users", output)
        self.assertEqual(list(store.users), [ServiceTestCase.SAM.user_id])

    def test_asks_again_after_a_mismatch_or_a_short_password(self) -> None:
        store = FakeUserStore()
        secrets = ["a long enough password", "a different password", "short", "short",
                   "a long enough password", "a long enough password"]
        code, output = self.run_prompts(store, ["admin"], secrets)
        self.assertEqual(code, 0)
        self.assertIn("don't match", output)
        self.assertIn("at least 12", output)

    def test_gives_up_after_three_bad_usernames(self) -> None:
        store = FakeUserStore()
        code, output = self.run_prompts(store, ["bad name", "", "x" * 101], [])
        self.assertEqual(code, 1)
        self.assertIn("No account was created", output)
        self.assertEqual(store.users, {})


if __name__ == "__main__":
    unittest.main()
