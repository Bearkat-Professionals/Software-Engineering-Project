"""Store and find sent-alert history for spec 7.5 and AT-12.

The history is append-only. Before sending an alert, callers can use
was_sent() with the same event, recipient and device/scan scope to avoid a
repeat notification. Events that may happen again can pass ``since`` to limit
the duplicate check to the current notification window.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import Repository
from backend.records import AlertHistory


class AlertHistoryStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._history = Repository(engine, AlertHistory)

    def store(self, history: AlertHistory, unit: Any = None) -> AlertHistory:
        """Append one sent alert. The database id is filled in on return."""
        if history.sent_at is None:
            history.sent_at = datetime.now()
        return self._history.create(history, unit)

    def find(self, alert_history_id: int, unit: Any = None) -> AlertHistory | None:
        """Find one history row by its database id."""
        return self._history.get(alert_history_id, unit)

    def find_delivery(self, event_id: int, recipient_email: str,
                      device_id: int | None = None, scan_id: int | None = None,
                      unit: Any = None,
                      since: datetime | None = None) -> AlertHistory | None:
        """Return the latest matching delivery, optionally at or after ``since``.

        Both scope ids may be None for a system-wide event such as a failed
        backup. A flagged-device alert normally has device_id, while a failed
        scan alert normally has scan_id.
        """
        sql = (
            "SELECT `alert_history_id`, `event_id`, `recipient_email`, `sent_at`, "
            "`device_id`, `scan_id` FROM `alert_history` "
            "WHERE `event_id` = %s AND `recipient_email` = %s "
            "AND `device_id` <=> %s AND `scan_id` <=> %s"
        )
        params: list[Any] = [event_id, recipient_email, device_id, scan_id]
        if since is not None:
            sql += " AND `sent_at` >= %s"
            params.append(since)
        sql += " ORDER BY `sent_at` DESC, `alert_history_id` DESC LIMIT 1"

        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone()
        return AlertHistory.from_row(row) if row else None


    def was_sent(self, event_id: int, recipient_email: str,
                 device_id: int | None = None, scan_id: int | None = None,
                 unit: Any = None,
                 since: datetime | None = None) -> bool:
        """Whether this alert was delivered, optionally at or after ``since``."""
        return self.find_delivery(
            event_id, recipient_email, device_id, scan_id,
            unit=unit, since=since) is not None


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
