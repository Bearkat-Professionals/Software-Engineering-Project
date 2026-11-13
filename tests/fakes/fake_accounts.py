"""Fake accounts for the controller tests: no database, no argon2.

    users = FakeUsers(SAM, VAL)
    access = FakeAccess(users)          # signs in anyone in `users` with PASSWORD
    client.post("/api/login", json={"username": "sam", "password": PASSWORD})
"""
from __future__ import annotations

from dataclasses import replace

from backend.records import User
from backend.records.enums import AccountStatus
from backend.services.access_control.access_control_service import (DISABLED_LOGIN, WRONG_LOGIN,
                                                                     LoginResult)

PASSWORD = "correct horse battery staple"
SECRET_KEY = "k" * 64                   # long enough for check_secret_key


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

    def authenticate(self, username: str, password: str) -> LoginResult:
        user = self.users.find_by_username(username)
        if user is None or password != PASSWORD:
            return LoginResult(None, WRONG_LOGIN)
        if user.account_status != AccountStatus.ACTIVE:
            return LoginResult(None, DISABLED_LOGIN)
        return LoginResult(user)


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds