"""3.3.1 Find User and 3.3.2 Store User.

Only the password HASH is ever stored (SEC-02): callers hash the password
before building the User. Every store writes an audit entry (3.4) in the same
transaction, so a user change is never saved without its audit record.

The audit snapshot comes from User.to_dict(), which drops password_hash, so
the hash is never copied into the audit log either.

    users = UserStore(engine)
    admin = users.store(User(username="admin", password_hash=hashed, role=UserRole.ADMIN))
    users.find_by_username("admin")
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import User
from backend.records.enums import AuditAction


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

    # ---- 3.3.2 Store User --------------------------------------------------------

    def store(self, user: User, changed_by: int | None = None, unit: Any = None) -> User:
        """Create the user (no user_id yet) or update it, plus its audit entry.

        changed_by is the user making the change. Left out, the user is
        recorded as changing their own record (e.g. the very first account).
        """
        if not user.password_hash:
            raise ValueError("a user needs a password hash")

        with _unit_of_work(self._engine, unit) as cursor:
            if user.pk is None:
                self._users.create(user, cursor)
                actor = user.pk if changed_by is None else changed_by
                self._audit.record(actor, AuditAction.INSERT, None, user, cursor)
            else:
                old = self._users.get(user.pk, cursor)
                if old is None:
                    raise RecordNotFoundError(f"app_user {user.pk} does not exist")
                self._users.update(user, cursor)
                actor = user.pk if changed_by is None else changed_by
                self._audit.record(actor, AuditAction.UPDATE, old, user, cursor)
        return user


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
