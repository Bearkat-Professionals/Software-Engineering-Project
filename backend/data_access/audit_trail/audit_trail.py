"""3.4 Audit trail: append-only record of who changed what, old and new values, and when.

Built on the generic Repository (#13). Other data-access modules call record()
inside the same unit of work as the change it describes, so the change and its
audit entry commit or roll back together:

    with engine.unit_of_work() as unit:
        old = devices.get(device_id, unit)
        devices.update(changed, unit)
        audit.record(user_id, AuditAction.UPDATE, old, changed, unit)

There is deliberately no update or delete: audit entries are never changed.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import Repository
from backend.records.audit_log import AuditLog
from backend.records.entity import Entity
from backend.records.enums import AuditAction


class AuditTrail:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._logs = Repository(engine, AuditLog)

    def record(self, user_id: int, action: AuditAction,
               before: Entity | None, after: Entity | None, unit: Any) -> AuditLog:
        """Write one audit entry inside the caller's unit of work (required).

        INSERT: before=None, after=new record
        UPDATE: before=record as loaded, after=record as saved
        DELETE: before=record as loaded, after=None
        """
        if unit is None:
            raise ValueError("record() must run inside the caller's unit of work")
        target = after or before
        if target is None or target.pk is None:
            raise ValueError("Audit entry needs a saved record")
        entry = AuditLog(
            user_id=user_id,
            record_type=target.TABLE,
            record_id=target.pk,
            action=action,
            old_value=_snapshot(before),
            new_value=_snapshot(after),
            changed_at=datetime.now(),   # schema has no DEFAULT; DATETIME(6) keeps microseconds
        )
        return self._logs.create(entry, unit)

    def history(self, record_type: str, record_id: int, unit: Any = None) -> list[AuditLog]:
        """Every entry for one record, oldest first."""
        return self._logs.list(unit, record_type=record_type, record_id=record_id)

    def by_user(self, user_id: int, unit: Any = None) -> list[AuditLog]:
        """Every entry made by one user, oldest first."""
        return self._logs.list(unit, user_id=user_id)

    def latest(self, record_type: str, record_id: int, unit: Any = None) -> AuditLog | None:
        """Most recent entry for one record; used by edit-conflict checks (OOD 3.2)."""
        columns = ", ".join(f"`{c}`" for c in AuditLog.column_names())
        sql = (f"SELECT {columns} FROM `audit_log` "
               "WHERE `record_type` = %s AND `record_id` = %s "
               "ORDER BY `changed_at` DESC, `audit_log_id` DESC LIMIT 1")
        with self._cursor(unit) as cursor:
            cursor.execute(sql, (record_type, record_id))
            row = cursor.fetchone()
        return AuditLog.from_row(row) if row else None

    # ------ helpers -----------------------------------------------------------

    @contextmanager
    def _cursor(self, unit: Any) -> Generator[Any, None, None]:
        """Use the caller's unit of work if one was passed, otherwise open one."""
        if unit is not None:
            yield unit
        else:
            with self._engine.unit_of_work() as cursor:
                yield cursor


def _snapshot(entity: Entity | None) -> dict[str, Any] | None:
    """JSON-safe copy of an entity's columns, for old_value/new_value."""
    if entity is None:
        return None
    return {k: v for k, v in entity.to_dict().items() if k not in entity.CHILD_FIELDS}
