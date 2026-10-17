"""ALERT_SETTING: a user's alert preference.

Which events trigger the alert is a many-to-many link kept in the
ALERT_SETTING_EVENT table (alert_setting_id, event_id). That link table has no
class of its own: the data-access layer reads it into `events` and writes it
back from `events`.
"""
from dataclasses import dataclass, field
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import AlertEventName


@dataclass(kw_only=True)
class AlertSetting(Entity):
    TABLE: ClassVar[str] = "alert_setting"
    PK: ClassVar[str] = "alert_setting_id"
    LINK_TABLE: ClassVar[str] = "alert_setting_event"
    BOOL_FIELDS: ClassVar[tuple] = ("enabled",)
    CHILD_FIELDS: ClassVar[tuple] = ("events",)    # not a column: lives in LINK_TABLE

    alert_setting_id: int | None = None
    user_id: int                           # FK -> app_user (owner)
    recipient_email: str
    enabled: bool = True
    events: list[AlertEventName] = field(default_factory=list)