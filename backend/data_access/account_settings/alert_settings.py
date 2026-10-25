"""3.3.3 Store Alert Setting and 3.3.4 Find Alert Setting.

AlertSetting.events is not a column: each event is a row in the
ALERT_SETTING_EVENT link table, which stores event ids from ALERT_EVENT, not
names. Storing a setting saves its row and replaces its link rows in one
transaction; finding one reads the link rows back into `events`.

    settings = AlertSettingStore(engine)
    saved = settings.store(AlertSetting(user_id=1, recipient_email="me@example.com",
                                        events=[AlertEventName.SCAN_FAILED,
                                                AlertEventName.FLAGGED_DEVICE]))
    settings.find(saved.alert_setting_id).events   # the same two events

Alert history: the schema has no table for alerts that were sent, so nothing
here records them (see #17).
"""
from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from typing import Any, Generator, Iterable

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import Repository
from backend.records import AlertSetting
from backend.records.enums import AlertEventName


class UnknownAlertEventError(ValueError):
    """An event name that is not a row in ALERT_EVENT (spec 7.5)."""


class AlertSettingStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._settings = Repository(engine, AlertSetting)

    # ---- 3.3.3 Store Alert Setting ----------------------------------------------

    def store(self, setting: AlertSetting, unit: Any = None) -> AlertSetting:
        """Create the setting (no id yet) or update it, and make its event links
        match setting.events exactly. Nothing is saved if an event is unknown."""
        with _unit_of_work(self._engine, unit) as cursor:
            event_ids = self._event_ids(setting.events, cursor)    # check before writing anything
            if setting.pk is None:
                self._settings.create(setting, cursor)
            else:
                self._settings.update(setting, cursor)
            cursor.execute("DELETE FROM `alert_setting_event` WHERE `alert_setting_id` = %s",
                           (setting.pk,))
            for event_id in event_ids:
                cursor.execute("INSERT INTO `alert_setting_event` (`alert_setting_id`, `event_id`) "
                               "VALUES (%s, %s)", (setting.pk, event_id))
        return setting

    # ---- 3.3.4 Find Alert Setting -----------------------------------------------

    def find(self, alert_setting_id: int, unit: Any = None) -> AlertSetting | None:
        with _unit_of_work(self._engine, unit) as cursor:
            setting = self._settings.get(alert_setting_id, cursor)
            if setting is not None:
                setting.events = self._events_for(setting.pk, cursor)
        return setting

    def for_user(self, user_id: int, unit: Any = None) -> list[AlertSetting]:
        """Every alert setting one user owns, each with its events."""
        with _unit_of_work(self._engine, unit) as cursor:
            settings = self._settings.list(cursor, user_id=user_id)
            for setting in settings:
                setting.events = self._events_for(setting.pk, cursor)
        return settings

    # ---- helpers ---------------------------------------------------------------

    def _event_ids(self, events: Iterable[AlertEventName | str], cursor: Any) -> list[int]:
        """Event names -> ALERT_EVENT ids, in order, each event once."""
        names = list(dict.fromkeys(e.value if isinstance(e, Enum) else str(e) for e in events))
        if not names:
            return []
        placeholders = ", ".join(["%s"] * len(names))
        cursor.execute(f"SELECT `event_id`, `event_name` FROM `alert_event` "
                       f"WHERE `event_name` IN ({placeholders})", tuple(names))
        ids = {row["event_name"]: row["event_id"] for row in cursor.fetchall()}
        unknown = [name for name in names if name not in ids]
        if unknown:
            raise UnknownAlertEventError(f"unknown alert event(s): {', '.join(unknown)}")
        return [ids[name] for name in names]

    def _events_for(self, alert_setting_id: int, cursor: Any) -> list[AlertEventName]:
        cursor.execute("SELECT e.`event_name` FROM `alert_setting_event` AS link "
                       "JOIN `alert_event` AS e ON e.`event_id` = link.`event_id` "
                       "WHERE link.`alert_setting_id` = %s ORDER BY e.`event_id`",
                       (alert_setting_id,))
        return [AlertEventName(row["event_name"]) for row in cursor.fetchall()]


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
