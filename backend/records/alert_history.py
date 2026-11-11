"""ALERT_HISTORY: one immutable record for an alert that was sent."""
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from backend.records.entity import Entity


@dataclass(kw_only=True)
class AlertHistory(Entity):
    TABLE: ClassVar[str] = "alert_history"
    PK: ClassVar[str] = "alert_history_id"

    alert_history_id: int | None = None
    event_id: int
    recipient_email: str
    sent_at: datetime | None = None
    device_id: int | None = None
    scan_id: int | None = None
