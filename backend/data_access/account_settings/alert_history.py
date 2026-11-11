"""Store and find sent-alert history for spec 7.5 and AT-12.

The history is append-only. Before sending an alert, callers can use
was_sent() with the same event, recipient and device/scan scope to avoid a
repeat notification.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import Repository
from backend.records import AlertHistory


class AlertHistoryStore:
    def __init__(self, engine: DatabaseEngine):
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
                      unit: Any = None) -> AlertHistory | None:
        """Return the latest matching delivery, or None when it was not sent.

        Both scope ids may be None for a system-wide event such as a failed
        backup. A flagged-device alert normally has device_id, while a failed
        scan alert normally has scan_id.
        """
        matches = self._history.list(
            unit,
            event_id=event_id,
            recipient_email=recipient_email,
            device_id=device_id,
            scan_id=scan_id,
        )
        return matches[-1] if matches else None

    def was_sent(self, event_id: int, recipient_email: str,
                 device_id: int | None = None, scan_id: int | None = None,
                 unit: Any = None) -> bool:
        """Whether this exact alert delivery already exists (AT-12)."""
        return self.find_delivery(event_id, recipient_email, device_id, scan_id, unit) is not None
