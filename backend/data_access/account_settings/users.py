"""3.3.1 Find User and 3.3.2 Store User.

Only the password HASH is ever stored (SEC-02): callers hash the password
before building the User. Every store writes an audit entry (3.4) in the same
transaction, so a user change is never saved without its audit record.

The audit snapshot comes from User.to_dict(), which drops password_hash, so
the hash is never copied into the audit log. A changed password is recorded
as {"password_changed": true} instead (AT-15).

    users = UserStore(engine)
    admin = users.create_first_user(User(username="admin", password_hash=h, role=UserRole.ADMIN))
    users.store(User(username="sam", password_hash=h2), changed_by=admin.user_id)
    users.find_by_username("sam")
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Generator

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import User
from backend.records.enums import AccountStatus, AuditAction, UserRole


class FirstUserExistsError(Exception):
    """create_first_user() was called but the system already has users."""


class LastActiveAdminError(Exception):
    """A change would leave the application without an active administrator."""


class UserStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._users = Repository(engine, User)
        self._audit = AuditTrail(engine)

    # ---- 3.3.1 Find User -------------------------------------------------------

    def find(self, user_id: int, unit: Any = None) -> User | None:
        return self._users.get(user_id, unit)

    def find_by_username(self, username: str, unit: Any = None) -> User | None:
        """Login looks users up by name; the schema makes usernames unique."""
        matches = self._users.list(unit, username=username)
        return matches[0] if matches else None

    def list(self, unit: Any = None) -> list[User]:
        """Every account in stable id order for the admin Users screen."""
        return self._users.list(unit)

    # ---- 3.3.2 Store User --------------------------------------------------------

    def store(self, user: User, changed_by: int, unit: Any = None) -> User:
        """Create the user (no user_id yet) or update it, plus its audit entry.

        changed_by is the user making the change (e.g. the admin), and is
        required so an edit is never attributed to the wrong person.
        """
        _require_hash(user)
        with _unit_of_work(self._engine, unit) as cursor:
            if user.pk is None:
                self._users.create(user, cursor)
                self._audit.record(changed_by, AuditAction.INSERT, None, user, cursor)
            else:
                old = self._users.get(user.pk, cursor)
                if old is None:
                    raise RecordNotFoundError(f"app_user {user.pk} does not exist")
                self._users.update(user, cursor)
                extra = {"password_changed": True} if old.password_hash != user.password_hash else None
                self._audit.record(changed_by, AuditAction.UPDATE, old, user, cursor, extra=extra)
        return user

    def apply_account_change(self, user_id: int, changed_by: int, *,
                             role: UserRole | None = None,
                             account_status: AccountStatus | None = None,
                             password_hash: str | None = None) -> User:
        """Apply one admin account change to a freshly loaded row.

        All account-management changes share a database lock. The active-admin
        count and the update therefore happen as one serialized transaction, so
        two requests cannot both observe another active admin and remove the last
        two at the same time.
        """
        with self._engine.named_lock("katscan_manage_users", timeout_s=5) as cursor:
            current = self.find(user_id, cursor)
            if current is None:
                raise RecordNotFoundError(f"app_user {user_id} does not exist")

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
            if removes_active_admin:
                active_admins = self._users.count(
                    cursor, role=UserRole.ADMIN, account_status=AccountStatus.ACTIVE)
                if active_admins <= 1:
                    raise LastActiveAdminError("the last active admin cannot be changed")

            return self.store(changed, changed_by=changed_by, unit=cursor)

    def create_first_user(self, user: User, unit: Any = None) -> User:
        """Create the very first account, which records itself as its creator.

        Refused once any user exists, so this can't be used to add accounts
        without an accountable changed_by.
        """
        _require_hash(user)
        if user.pk is not None:
            raise ValueError("the first user must be new")
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM `app_user` FOR UPDATE")
            if cursor.fetchone()["n"] > 0:
                raise FirstUserExistsError("users already exist; use store() with changed_by")
            self._users.create(user, cursor)
            self._audit.record(user.pk, AuditAction.INSERT, None, user, cursor)
        return user


def _require_hash(user: User) -> None:
    if not user.password_hash:
        raise ValueError("a user needs a password hash")


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
