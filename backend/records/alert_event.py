"""ALERT_EVENT: lookup table of events that can trigger an email alert."""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import AlertEventName


@dataclass(kw_only=True)
class AlertEvent(Entity):
    TABLE: ClassVar[str] = "alert_event"
    PK: ClassVar[str] = "event_id"
    ENUM_FIELDS: ClassVar[dict] = {"event_name": AlertEventName}

    event_id: int | None = None
    event_name: AlertEventName