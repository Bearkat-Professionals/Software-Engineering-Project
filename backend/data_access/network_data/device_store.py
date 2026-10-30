"""3.1.1 Store Device, 3.1.2 Find Devices and 3.1.3 Delete Device.

The MAC address is a device's identity (DI-01): storing a device whose MAC is
already known updates that row instead of adding a second one, so an IP change
never creates a duplicate (FR-04). A randomized MAC is never merged with a
fixed one or with a different MAC (DI-02); the same randomized MAC seen again
updates its own row (design 6.2).

Every create and update writes an audit entry in the same transaction (POL-02).
Edit conflicts (OOD 3.2): there is no version column. A caller that edits a
device it loaded earlier passes loaded_at; if the device's latest audit entry
is newer than that, someone else changed it in between and the save is refused.

    devices = DeviceStore(engine)
    saved = devices.store(Device(mac_address="28:6F:B9:00:00:01"), changed_by=user_id)
    devices.store(edited, changed_by=user_id, loaded_at=when_the_form_was_loaded)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator
from datetime import datetime
from enum import Enum

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import Device, AuditLog
from backend.records.enums import AuditAction

# Tables whose rows belong to a device and are removed with it (ON DELETE CASCADE)
_CHILD_TABLES = {
    "observations_removed": "scan_observation",
    "evidence_removed": "identification_evidence",
}

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200
# Columns the device list can be narrowed by (3.1.2 "by criteria")
LIST_CRITERIA = ("network_status", "flagged_status", "trusted_status", "is_randomized_mac",
                 "vendor_id", "device_type_id")

@dataclass(frozen=True)
class DeviceListing:
    """One row of the device list: the device plus its vendor and type names."""
    device: Device
    vendor_name: str
    device_type_name: str

@dataclass(frozen=True)
class DevicePage:
    """One page of the device list, and how many devices match in total."""
    devices: list[DeviceListing]
    page: int
    per_page: int
    total: int

class EditConflictError(Exception):
    """The device was changed by someone else after the caller loaded it."""

    def __init__(self, device_id: int, latest: AuditLog):
        self.device_id = device_id
        self.changed_by = latest.user_id
        self.changed_at = latest.changed_at
        super().__init__(f"device {device_id} was changed by user {latest.user_id} "
                         f"at {latest.changed_at} after it was loaded")

class DeviceStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._devices = Repository(engine, Device)
        self._audit = AuditTrail(engine)

    # ---- 3.1.2 Find Devices ------------------------------------------------------

    def find(self, device_id: int, unit: Any = None) -> Device | None:
        return self._devices.get(device_id, unit)

    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None) -> Device | None:
        """The device with this MAC. Fixed and randomized MACs are looked up
        separately, so the two are never confused (DI-02). If several rows share
        one randomized MAC, the newest is returned."""
        matches = self._devices.list(unit, mac_address=mac_address, is_randomized_mac=is_randomized_mac)
        return matches[-1] if matches else None

    def list_page(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE, unit: Any = None,
                  **criteria: Any) -> DevicePage:
        """One page of the device list, newest sighting first, each device with
        its vendor and type NAMES (one query, not one lookup per device).

        criteria narrow the list by equality, e.g. flagged_status=True or
        network_status=NetworkStatus.ONLINE; only the columns in LIST_CRITERIA
        are allowed. Retired vendors and types still show their names (DI-06).
        """
        if page < 1 or not 1 <= per_page <= MAX_PER_PAGE:
            raise ValueError(f"page must be 1 or more and per_page 1 to {MAX_PER_PAGE}")
        where, params = _where(criteria)
        columns = ", ".join(f"d.`{name}`" for name in Device.column_names())
        sql = (f"SELECT {columns}, v.`vendor_name`, t.`category_name` "
               "FROM `device` d "
               "JOIN `vendor` v ON v.`vendor_id` = d.`vendor_id` "
               "JOIN `device_type` t ON t.`device_type_id` = d.`device_type_id` "
               f"{where} "
               "ORDER BY d.`last_seen` DESC, d.`device_id` DESC "
               "LIMIT %s OFFSET %s")
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(f"SELECT COUNT(*) AS n FROM `device` d {where}", params)
            total = cursor.fetchone()["n"]
            cursor.execute(sql, (*params, per_page, (page - 1) * per_page))
            rows = cursor.fetchall()
        listings = [DeviceListing(Device.from_row(row), row["vendor_name"], row["category_name"])
                    for row in rows]
        return DevicePage(listings, page, per_page, total)

    # ---- 3.1.1 Store Device ------------------------------------------------------

    def store(self, device: Device, changed_by: int, unit: Any = None,
              *, loaded_at: datetime | None = None) -> Device:
        """Insert the device, or update it if it has an id or its MAC is already
        known, plus its audit entry. Returns the device as stored, including the
        values the database filled in (e.g. vendor 1, "unknown").

        loaded_at: when the caller read the device it is now saving. If given,
        the save is refused with EditConflictError when the device changed after
        that. Scans leave it out; the edit page (M5) passes it."""
        with _unit_of_work(self._engine, unit) as cursor:
            if device.pk is None:
                same = self.find_by_mac(device.mac_address, device.is_randomized_mac, cursor)
                if same is not None:
                    device.device_id = same.pk                # known MAC: update, never duplicate

            if device.pk is None:
                old = None
                self._devices.create(device, cursor)
                action = AuditAction.INSERT
            else:
                self._lock(device.pk, cursor)          # nobody else saves it until we commit
                old = self._devices.get(device.pk, cursor)
                if old is None:
                    raise RecordNotFoundError(f"device {device.pk} does not exist")
                if loaded_at is not None:
                    self._check_unchanged_since(device.pk, loaded_at, cursor)
                self._devices.update(device, cursor)
                action = AuditAction.UPDATE

            stored = self._devices.get(device.pk, cursor)     # read back, with database defaults
            self._audit.record(changed_by, action, old, stored, cursor)
        return stored

    # ---- 3.1.3 Delete Device -----------------------------------------------------

    def delete(self, device_id: int, changed_by: int, unit: Any = None,
               *, loaded_at: datetime | None = None) -> dict[str, int]:
        """Delete the device. Its observations and evidence go with it (DI-05):
        the database cascades them, so none are left orphaned. The audit entry
        keeps a copy of the deleted device and how many of each were removed.

        Returns those counts, e.g. {"observations_removed": 3, "evidence_removed": 2}."""
        with _unit_of_work(self._engine, unit) as cursor:
            self._lock(device_id, cursor)
            old = self._devices.get(device_id, cursor)
            if old is None:
                raise RecordNotFoundError(f"device {device_id} does not exist")
            if loaded_at is not None:
                self._check_unchanged_since(device_id, loaded_at, cursor)
            removed = self._count_children(device_id, cursor)
            self._devices.delete(device_id, cursor)           # cascades to observations and evidence
            self._audit.record(changed_by, AuditAction.DELETE, old, None, cursor, extra=removed)
        return removed

    # ---- helpers -----------------------------------------------------------------

    @staticmethod
    def _lock(device_id: int, cursor: Any) -> None:
        """Lock the device row until this transaction ends, so two saves of the
        same device run one after the other instead of at the same time."""
        cursor.execute("SELECT `device_id` FROM `device` WHERE `device_id` = %s FOR UPDATE", (device_id,))
        cursor.fetchall()

    def _check_unchanged_since(self, device_id: int, loaded_at: datetime, cursor: Any) -> None:
        latest = self._audit.latest(Device.TABLE, device_id, cursor)
        if latest is not None and latest.changed_at > loaded_at:
            raise EditConflictError(device_id, latest)

    @staticmethod
    def _count_children(device_id: int, cursor: Any) -> dict[str, int]:
        """How many rows the delete will cascade to, for the audit entry."""
        counts = {}
        for key, table in _CHILD_TABLES.items():
            cursor.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE `device_id` = %s", (device_id,))
            counts[key] = cursor.fetchone()["n"]
        return counts

def _where(criteria: dict[str, Any]) -> tuple[str, list[Any]]:
    """WHERE clause and parameters for the device list's criteria."""
    conditions, params = [], []
    for name, value in criteria.items():
        if name not in LIST_CRITERIA:
            raise ValueError(f"the device list can't be filtered by '{name}'")
        conditions.append(f"d.`{name}` = %s")
        params.append(value.value if isinstance(value, Enum) else value)
    return ("WHERE " + " AND ".join(conditions)) if conditions else "", params

@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
