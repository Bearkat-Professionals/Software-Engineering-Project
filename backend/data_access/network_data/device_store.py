"""3.1.1 Store Device, 3.1.2 Find Devices (search with FR-10 criteria, text and sorting) and 3.1.3 Delete Device.

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
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Generator

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import AuditLog, Device
from backend.records.enums import AuditAction, NetworkStatus

# Tables whose rows belong to a device and are removed with it (ON DELETE CASCADE)
_CHILD_TABLES = {
    "observations_removed": "scan_observation",
    "evidence_removed": "identification_evidence",
}

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200
IN_CHUNK = 1000           #Javier Changed
# Columns the device list can be narrowed by exact match (3.1.2 "by criteria");
# DeviceSearch has a field with each of these names
LIST_CRITERIA = ("network_status", "flagged_status", "trusted_status", "is_randomized_mac",
                 "vendor_id", "device_type_id")
# The only things the list can be sorted by. A request names a key; only the
# fixed expression next to it ever goes into the SQL.
SORT_COLUMNS: dict[str, str] = {
    "last_seen": "d.`last_seen`",
    "name": "d.`device_name`",
    "ip": "INET_ATON(d.`current_ip`)",         # numeric, so .9 comes before .10; IPv6 counts as no IP
    "confidence": "d.`confidence_score`",
    "vendor": "v.`vendor_name`",
    "type": "t.`category_name`",
}
# Columns the text search looks in
TEXT_COLUMNS = ("device_name", "hostname", "current_ip", "mac_address")


@dataclass(frozen=True)
class DeviceSearch:
    """What the device list is narrowed and sorted by (FR-10). Every filter is
    optional; None means "any". Checked when created, so a bad search never
    reaches the database."""
    device_type_id: int | None = None
    vendor_id: int | None = None
    network_status: NetworkStatus | None = None
    flagged_status: bool | None = None
    trusted_status: bool | None = None
    is_randomized_mac: bool | None = None
    confidence_min: int | None = None
    confidence_max: int | None = None
    seen_from: date | None = None              # whole days: from 00:00 on seen_from
    seen_to: date | None = None                # to the end of seen_to
    text: str | None = None                    # part of the name, hostname, IP or MAC
    sort: str = "last_seen"
    descending: bool = True

    def __post_init__(self) -> None:
        for name in ("seen_from", "seen_to"):
            value = getattr(self, name)
            if isinstance(value, datetime):                       # a datetime is a date too; keep the day
                object.__setattr__(self, name, value.date())
        if self.text is not None:
            object.__setattr__(self, "text", self.text.strip() or None)    # blank means no text search

        for name in ("confidence_min", "confidence_max"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 100:
                raise ValueError(f"{name} must be 0 to 100")
        if self.confidence_min is not None and self.confidence_max is not None \
                and self.confidence_min > self.confidence_max:
            raise ValueError("confidence_min is more than confidence_max")
        if self.seen_from is not None and self.seen_to is not None and self.seen_from > self.seen_to:
            raise ValueError("seen_from is after seen_to")
        if self.sort not in SORT_COLUMNS:
            raise ValueError(f"the device list can't be sorted by '{self.sort}'")

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

# One device row plus its vendor and type NAMES, for the list and the edit form. Both
# columns are NOT NULL (default 1, "unknown"), so a plain JOIN never drops a device.
_LISTING_SELECT = (
    "SELECT " + ", ".join(f"d.`{name}`" for name in Device.column_names()) + ", "
    "v.`vendor_name`, t.`category_name` "
    "FROM `device` d "
    "JOIN `vendor` v ON v.`vendor_id` = d.`vendor_id` "
    "JOIN `device_type` t ON t.`device_type_id` = d.`device_type_id` "
)


def _listing(row: dict[str, Any]) -> DeviceListing:
    return DeviceListing(Device.from_row(row), row["vendor_name"], row["category_name"])

class DeviceStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._devices = Repository(engine, Device)
        self._audit = AuditTrail(engine)

    # ---- 3.1.2 Find Devices ------------------------------------------------------

    def unit_of_work(self) -> Any:
        """A transaction for SH-4 when its caller did not already provide one."""
        return self._engine.unit_of_work()

    def find(self, device_id: int, unit: Any = None, *, lock: bool = False) -> Device | None:
        """Find one device, optionally locking it through the caller's unit."""
        if lock and unit is None:
            raise ValueError("a unit of work is required to lock a device")
        columns = ", ".join(f"`{name}`" for name in Device.column_names())
        sql = f"SELECT {columns} FROM `device` WHERE `device_id` = %s"
        if lock:
            sql += " FOR UPDATE"
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(sql, (device_id,))
            row = cursor.fetchone()
        return Device.from_row(row) if row else None

    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None, *, lock: bool = False) -> Device | None:
        """The device with this MAC. Fixed and randomized MACs are looked up
        separately, so the two are never confused (DI-02). If several rows share
        one randomized MAC, the newest is returned.

        ``lock=True`` reads with ``FOR UPDATE`` and therefore requires the
        caller's unit of work. The lock then remains held through the save.
        """
        if lock and unit is None:
            raise ValueError("a unit of work is required to lock a device")
        columns = ", ".join(f"`{name}`" for name in Device.column_names())
        sql = (f"SELECT {columns} FROM `device` "
               "WHERE `mac_address` = %s AND `is_randomized_mac` = %s "
               "ORDER BY `device_id` DESC LIMIT 1")
        if lock:
            sql += " FOR UPDATE"
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(sql, (mac_address, is_randomized_mac))
            row = cursor.fetchone()
        return Device.from_row(row) if row else None

    def find_listing(self, device_id: int, unit: Any = None) -> DeviceListing | None:
        """One device with its vendor and type NAMES, exactly as the device list shows it
        (retired vendors and types included, DI-06). None if there's no such device."""
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(_LISTING_SELECT + "WHERE d.`device_id` = %s", (device_id,))
            row = cursor.fetchone()
        return _listing(row) if row is not None else None

    def search(self, search: DeviceSearch = DeviceSearch(), page: int = 1,
               per_page: int = DEFAULT_PER_PAGE, unit: Any = None) -> DevicePage:
        """One page of the devices matching the search, each with its vendor and
        type NAMES, and how many match in total. Devices with no value for the
        sort column come last either way; device_id breaks ties, so paging never
        repeats or skips a device. Retired vendors and types still show (DI-06)."""
        if page < 1 or not 1 <= per_page <= MAX_PER_PAGE:
            raise ValueError(f"page must be 1 or more and per_page 1 to {MAX_PER_PAGE}")
        where, params = _search_where(search)
        sort = SORT_COLUMNS[search.sort]
        direction = "DESC" if search.descending else "ASC"
        columns = ", ".join(f"d.`{name}`" for name in Device.column_names())
        sql = (f"{_LISTING_SELECT}{where} "
               f"ORDER BY {sort} IS NULL, {sort} {direction}, d.`device_id` {direction} "
               "LIMIT %s OFFSET %s")
        with _unit_of_work(self._engine, unit) as cursor:
            # The WHERE only uses device columns, so the count needs no joins
            cursor.execute(f"SELECT COUNT(*) AS n FROM `device` d {where}", params)
            total = cursor.fetchone()["n"]
            cursor.execute(sql, (*params, per_page, (page - 1) * per_page))
            rows = cursor.fetchall()
        return DevicePage([_listing(row) for row in rows], page, per_page, total)


    def search_all(self, search: DeviceSearch, limit: int, unit: Any = None) -> list[DeviceListing]:
        where, params = _search_where(search)
        sql = _LISTING_SELECT + where + " " + _order_by(search) + " LIMIT %s"
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(sql, params + [limit + 1])
            rows = cursor.fetchall()

        listings = []
        for row in rows:
            listings.append(_listing(row))
        return listings

    def listings_for(self, device_ids: list[int], unit: Any = None) -> list[DeviceListing]:
        ids = sorted(set(device_ids))
        listings = []
        with _unit_of_work(self._engine, unit) as cursor:
            for start in range(0, len(ids), IN_CHUNK):
                chunk = ids[start:start + IN_CHUNK]
                sql = _LISTING_SELECT + "WHERE d.`device_id` IN (" + _marks(chunk) + ") ORDER BY d.`device_id`"
                cursor.execute(sql, chunk)
                for row in cursor.fetchall():
                    listings.append(_listing(row))
        return listings








    
    def list_page(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE, unit: Any = None,
                  **criteria: Any) -> DevicePage:
        """One page of the device list, newest sighting first. criteria narrow it
        by equality, e.g. flagged_status=True; only the columns in LIST_CRITERIA
        are allowed. Same as search() with only those filters."""
        unknown = sorted(set(criteria) - set(LIST_CRITERIA))
        if unknown:
            raise ValueError(f"the device list can't be filtered by '{unknown[0]}'")
        return self.search(DeviceSearch(**criteria), page, per_page, unit)

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
                same = self.find_by_mac(device.mac_address, device.is_randomized_mac, cursor, lock=True)
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
        conflicts = [entry for entry in self._audit.since(Device.TABLE, device_id, loaded_at, cursor)
                     if entry.action is AuditAction.DELETE or _changes_user_field(entry)]
        if conflicts:
            raise EditConflictError(device_id, conflicts[-1])

    @staticmethod
    def _count_children(device_id: int, cursor: Any) -> dict[str, int]:
        """How many rows the delete will cascade to, for the audit entry."""
        counts = {}
        for key, table in _CHILD_TABLES.items():
            cursor.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE `device_id` = %s", (device_id,))
            counts[key] = cursor.fetchone()["n"]
        return counts



def _order_by(search: DeviceSearch) -> str:            #Javier Changed
    sort = SORT_COLUMNS[search.sort]
    if search.descending:
        direction = "DESC"
    else:
        direction = "ASC"
    return "ORDER BY " + sort + " IS NULL, " + sort + " " + direction + ", d.`device_id` " + direction


def _marks(ids: list[int]) -> str:
    marks = []
    for device_id in ids:
        marks.append("%s")
    return ", ".join(marks)




def _search_where(search: DeviceSearch) -> tuple[str, list[Any]]:
    """WHERE clause and parameters for a search. Every value is a parameter;
    the SQL text is built only from fixed column names."""
    conditions: list[str] = []
    params: list[Any] = []
    for name in LIST_CRITERIA:                                        # exact matches
        value = getattr(search, name)
        if value is not None:
            conditions.append(f"d.`{name}` = %s")
            params.append(value.value if isinstance(value, Enum) else value)
    if search.confidence_min is not None:
        conditions.append("d.`confidence_score` >= %s")
        params.append(search.confidence_min)
    if search.confidence_max is not None:
        conditions.append("d.`confidence_score` <= %s")
        params.append(search.confidence_max)
    if search.seen_from is not None:
        conditions.append("d.`last_seen` >= %s")
        params.append(datetime.combine(search.seen_from, time.min))
    if search.seen_to is not None:                                    # before the next day starts
        conditions.append("d.`last_seen` < %s")
        params.append(datetime.combine(search.seen_to + timedelta(days=1), time.min))
    if search.text is not None:
        matches = " OR ".join(f"d.`{column}` LIKE %s ESCAPE '\\\\'" for column in TEXT_COLUMNS)
        conditions.append(f"({matches})")
        params.extend([f"%{_escape_like(search.text)}%"] * len(TEXT_COLUMNS))
    return ("WHERE " + " AND ".join(conditions)) if conditions else "", params


def _escape_like(value: str) -> str:
    """Make \\, % and _ match themselves in a LIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _changes_user_field(entry: AuditLog) -> bool:
    """True when an audit snapshot changed something a person can edit."""
    before = entry.old_value or {}
    after = entry.new_value or {}
    return any(before.get(name) != after.get(name) for name in Device.USER_FIELDS)

@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
