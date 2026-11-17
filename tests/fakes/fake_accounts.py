"""Fake accounts for the controller tests: no database, no argon2.

    users = FakeUsers(SAM, VAL)
    access = FakeAccess(users)          # signs in anyone in `users` with PASSWORD
    client.post("/api/login", json={"username": "sam", "password": PASSWORD})
"""
from __future__ import annotations

from dataclasses import replace

from backend.records import User
from backend.records.enums import AccountStatus, UserRole
from backend.services.access_control.access_control_service import (ACCOUNT_NOT_FOUND,
                                                                     DISABLED_LOGIN, WRONG_LOGIN,
                                                                     AccountResult, LoginResult)

PASSWORD = "correct horse battery staple"
SECRET_KEY = "k" * 64                   # long enough for check_secret_key
DUMMY_HASH = "$argon2id$fake-controller-test-hash"


class FakeUsers:
    """UserStore.find / find_by_username in memory. working=False acts like the database is down."""

    def __init__(self, *users: User) -> None:
        self.users = {user.user_id: user for user in users}
        self.working = True

    def find(self, user_id: int) -> User | None:
        if not self.working:
            raise RuntimeError("database went away")
        return self.users.get(user_id)

    def find_by_username(self, username: str) -> User | None:
        return next((user for user in self.users.values() if user.username == username), None)

    def disable(self, user: User) -> None:
        self.users[user.user_id] = replace(user, account_status=AccountStatus.DISABLED)


class FakeAccess:
    """AccessControlService.authenticate without hashing: PASSWORD is everyone's password."""

    def __init__(self, users: FakeUsers) -> None:
        self.users = users
        self.changes: list[tuple[str, int, int]] = []

    def authenticate(self, username: str, password: str) -> LoginResult:
        user = self.users.find_by_username(username)
        if user is None or password != PASSWORD:
            return LoginResult(None, WRONG_LOGIN)
        if user.account_status != AccountStatus.ACTIVE:
            return LoginResult(None, DISABLED_LOGIN)
        return LoginResult(user)

    def list_users(self) -> list[User]:
        return [self.users.users[user_id] for user_id in sorted(self.users.users)]

    def create_user(self, username: str, password: str, role: str,
                    created_by: int) -> AccountResult:
        if not username or not password or role not in {item.value for item in UserRole}:
            return AccountResult(None, ("The account values are invalid.",))
        user = User(user_id=max(self.users.users, default=0) + 1, username=username,
                    password_hash=DUMMY_HASH, role=UserRole(role))
        self.users.users[user.user_id] = user
        self.changes.append(("create", user.user_id, created_by))
        return AccountResult(user)

    def set_role(self, user_id: int, role: str, changed_by: int) -> AccountResult:
        user = self.users.find(user_id)
        if user is None:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        user = replace(user, role=UserRole(role))
        self.users.users[user_id] = user
        self.changes.append(("role", user_id, changed_by))
        return AccountResult(user)

    def set_status(self, user_id: int, status: str, changed_by: int) -> AccountResult:
        user = self.users.find(user_id)
        if user is None:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        user = replace(user, account_status=AccountStatus(status))
        self.users.users[user_id] = user
        self.changes.append(("status", user_id, changed_by))
        return AccountResult(user)

    def reset_password(self, user_id: int, password: str, changed_by: int) -> AccountResult:
        user = self.users.find(user_id)
        if user is None:
            return AccountResult(None, (ACCOUNT_NOT_FOUND,))
        user = replace(user, password_hash=DUMMY_HASH)
        self.users.users[user_id] = user
        self.changes.append(("password", user_id, changed_by))
        return AccountResult(user)

    def change_password(self, user: User, current_password: str,
                        new_password: str) -> AccountResult:
        if current_password != PASSWORD or not new_password:
            return AccountResult(None, ("The current password is incorrect.",))
        self.users.users[user.user_id] = replace(user, password_hash=DUMMY_HASH)
        self.changes.append(("own_password", user.user_id, user.user_id))
        return AccountResult(self.users.users[user.user_id])


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
