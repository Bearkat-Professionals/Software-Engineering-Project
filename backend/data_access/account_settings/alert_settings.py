"""3.3.3 Store Alert Setting and 3.3.4 Find Alert Setting.

AlertSetting.events is not a column: each event is a row in the
ALERT_SETTING_EVENT link table, which stores event ids from ALERT_EVENT, not
names. Storing a setting saves its row, replaces its link rows and writes an
audit entry (POL-02, SEC-06), all in one transaction. The audit entry's
old/new values include the events, so "events changed from X to Y" is visible.

    settings = AlertSettingStore(engine)
    saved = settings.store(AlertSetting(user_id=1, recipient_email="me@example.com",
                                        events=[AlertEventName.SCAN_FAILED,
                                                AlertEventName.FLAGGED_DEVICE]),
                           changed_by=1)
    settings.find(saved.alert_setting_id).events   # the same two events

Alert history: the schema has no table for alerts that were sent yet. It is
needed for spec 7.5 "don't repeat an alert" (AT-12) in M6; see the follow-up issue.
"""
from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
from typing import Any, Generator, Iterable

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import AlertSetting
from backend.records.enums import AlertEventName, AuditAction


class UnknownAlertEventError(ValueError):
    """An event name that is not a row in ALERT_EVENT (spec 7.5)."""


class AlertSettingStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._settings = Repository(engine, AlertSetting)
        self._audit = AuditTrail(engine)

    # ---- 3.3.3 Store Alert Setting ----------------------------------------------

    def store(self, setting: AlertSetting, changed_by: int, unit: Any = None) -> AlertSetting:
        """Create the setting (no id yet) or update it, make its event links match
        setting.events exactly, and audit the change. changed_by is the user
        making it, who may not be setting.user_id (e.g. an admin).
        Nothing is saved if an event is unknown."""
        with _unit_of_work(self._engine, unit) as cursor:
            events = self._resolve_events(setting.events, cursor)   # check before writing anything
            if setting.pk is None:
                old = None
                self._settings.create(setting, cursor)
                action = AuditAction.INSERT
            else:
                old = self.find(setting.pk, cursor)                 # row + events, for old_value
                if old is None:
                    raise RecordNotFoundError(f"alert_setting {setting.pk} does not exist")
                self._settings.update(setting, cursor)
                action = AuditAction.UPDATE

            cursor.execute("DELETE FROM `alert_setting_event` WHERE `alert_setting_id` = %s",
                           (setting.pk,))
            for event_id, _ in events:
                cursor.execute("INSERT INTO `alert_setting_event` (`alert_setting_id`, `event_id`) "
                               "VALUES (%s, %s)", (setting.pk, event_id))
            setting.events = [name for _, name in events]           # as stored: no duplicates, id order

            self._audit.record(changed_by, action, old, setting, cursor)
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

    def _resolve_events(self, events: Iterable[AlertEventName | str],
                        cursor: Any) -> list[tuple[int, AlertEventName]]:
        """Event names -> (ALERT_EVENT id, name), each event once, in id order."""
        names = list(dict.fromkeys(e.value if isinstance(e, Enum) else str(e) for e in events))
        if not names:
            return []
        placeholders = ", ".join(["%s"] * len(names))
        cursor.execute(f"SELECT `event_id`, `event_name` FROM `alert_event` "
                       f"WHERE `event_name` IN ({placeholders}) ORDER BY `event_id`", tuple(names))
        rows = cursor.fetchall()
        unknown = sorted(set(names) - {row["event_name"] for row in rows})
        if unknown:
            raise UnknownAlertEventError(f"unknown alert event(s): {', '.join(unknown)}")
        return [(row["event_id"], AlertEventName(row["event_name"])) for row in rows]

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
