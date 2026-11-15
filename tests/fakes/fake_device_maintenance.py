"""Fakes for testing 2.3.1 Device Record Maintenance without a database.

    engine = FakeEngine()                           # unit_of_work(); refuses any SQL
    devices = FakeMaintainedDevices([...], clock)   # FakeDeviceStore + find, delete and conflicts
    saver = SpySaver(devices, vendors, types)       # the real SH-4, recording every call
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.data_access.network_data import EditConflictError
from backend.data_access.repository import RecordNotFoundError
from backend.records import AuditLog, Device, User
from backend.records.enums import AuditAction
from backend.shared_modules.save_device_record import DeviceChanges, DeviceRecordSaver, SaveOutcome
from tests.fakes.fake_device_store import AuditEntry, FakeDeviceStore


class FakeClock:
    def __init__(self, now: datetime = datetime(2026, 11, 13, 9, 0, 0)) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float = 1) -> None:
        self.now += timedelta(minutes=minutes)


class FakeCursor:
    """The unit of work the stores are given. The service itself sends no SQL (the
    stores do that), so any query sent to it straight is a test failure."""

    def __init__(self, name: str) -> None:
        self.name = name

    def execute(self, sql: str, params: Any = None) -> None:
        raise AssertionError(f"the service sent SQL itself: {sql}")

    def __repr__(self) -> str:
        return self.name


class FakeEngine:
    """engine.unit_of_work() stand-in: counts commits and rollbacks."""

    def __init__(self) -> None:
        self.opened = 0
        self.committed = 0
        self.rolled_back = 0

    @contextmanager
    def unit_of_work(self):
        self.opened += 1
        unit = FakeCursor(f"unit-{self.opened}")
        try:
            yield unit
        except Exception:
            self.rolled_back += 1
            raise
        self.committed += 1


class FakeMaintainedDevices(FakeDeviceStore):
    """FakeDeviceStore plus find(id), delete() and the loaded_at check, like the real store."""

    def __init__(self, devices: Iterable[Device] = (), clock: FakeClock | None = None) -> None:
        super().__init__(devices)
        self.clock = clock or FakeClock()
        self.latest: dict[int, tuple[int, datetime]] = {}     # device id -> (user, when) of its last change
        self.children: dict[int, dict[str, int]] = {}         # device id -> rows the delete cascades to

    def find(self, device_id: int, unit: Any = None) -> Device | None:
        self.units_seen.append(unit)
        found = self.rows.get(device_id)
        return deepcopy(found) if found is not None else None

    def store(self, device: Device, changed_by: int, unit: Any = None,
              *, loaded_at: datetime | None = None) -> Device:
        if device.pk is not None:
            self._check_unchanged_since(device.pk, loaded_at)
        stored = super().store(device, changed_by, unit, loaded_at=loaded_at)
        self.latest[stored.pk] = (changed_by, self.clock())
        return stored

    def delete(self, device_id: int, changed_by: int, unit: Any = None,
               *, loaded_at: datetime | None = None) -> dict[str, int]:
        self.units_seen.append(unit)
        if device_id not in self.rows:
            raise RecordNotFoundError(f"device {device_id} does not exist")
        self._check_unchanged_since(device_id, loaded_at)
        removed = dict(self.children.get(device_id, {"observations_removed": 0, "evidence_removed": 0}))
        del self.rows[device_id]
        self.audit.append(AuditEntry(changed_by, AuditAction.DELETE, device_id, unit))
        return removed

    def changed_elsewhere(self, device_id: int, user: int, when: datetime) -> None:
        """Pretend another user changed the device at `when`."""
        self.latest[device_id] = (user, when)

    def _check_unchanged_since(self, device_id: int, loaded_at: datetime | None) -> None:
        if loaded_at is None or device_id not in self.latest:
            return
        user, when = self.latest[device_id]
        if when > loaded_at:
            raise EditConflictError(device_id, AuditLog(user_id=user, record_type="device", record_id=device_id,
                                                         action=AuditAction.UPDATE, changed_at=when))


class FakeUsers:
    """UserStore.find in memory, to name who caused a conflict."""

    def __init__(self, *users: User) -> None:
        self.users = {user.user_id: user for user in users}

    def find(self, user_id: int, unit: Any = None) -> User | None:
        return self.users.get(user_id)


@dataclass(frozen=True)
class SaveCall:
    changes: DeviceChanges
    acting_user_id: int
    level: Any
    unit: Any
    loaded_at: datetime | None


class SpySaver(DeviceRecordSaver):
    """The real SH-4, recording what it was asked to save."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[SaveCall] = []

    def save(self, changes: DeviceChanges, acting_user_id: int, level: Any,
             unit: Any = None, loaded_at: datetime | None = None) -> SaveOutcome:
        self.calls.append(SaveCall(changes, acting_user_id, level, unit, loaded_at))
        return super().save(changes, acting_user_id, level, unit, loaded_at)
