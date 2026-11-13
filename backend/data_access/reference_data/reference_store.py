"""3.2.2 Store Reference Record, 3.2.3 Retire Reference Record, 3.2.4 Check Reference In Use

One store for the three reference kinds that can be retired: vendors, device
types and device signatures (DI-06). The kind travels as data (design 8.2):
ReferenceStore(engine, Vendor) works exactly like ReferenceStore(engine, DeviceType).
OUI prefixes belong to a vendor and have their own store (oui_prefixes.py).

Every change writes an audit entry in the same transaction (POL-02, SEC-06).

    vendors = ReferenceStore(engine, Vendor)
    acme = vendors.store(Vendor(vendor_name="Acme"), changed_by=admin_id)
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Generator, Generic, TypeVar

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import AuditLog, DeviceSignature, DeviceType, Vendor
from backend.records.enums import AuditAction, RecordStatus
from backend.records.reference_entity import ReferenceEntity

T = TypeVar("T", bound=ReferenceEntity)

# The columns that make a record a duplicate of another one (DI-07). Vendor and
# device type names are also UNIQUE in the schema; signatures have no unique key,
# so this check is the only thing stopping the same pattern being added twice.
DUPLICATE_KEYS: dict[type[ReferenceEntity], tuple[str, ...]] = {
    Vendor: ("vendor_name",),
    DeviceType: ("category_name",),
    DeviceSignature: ("evidence_type", "pattern_value"),
}

# Rows that point at each kind of reference record (3.2.4). Used only to report
# usage: retiring never needs them to be zero, because nothing is deleted (DI-06).
USED_BY: dict[type[ReferenceEntity], tuple[tuple[str, str], ...]] = {
    Vendor: (("device", "vendor_id"),),
    DeviceType: (("device", "device_type_id"), ("device_signature", "device_type_id")),
    DeviceSignature: (("identification_evidence", "signature_id"),),
}

# The "unknown" vendor and device type are the defaults every new device gets
# (DI-03, seed file): renaming or retiring them would break that.
UNKNOWN_ID = 1
PROTECTED: set[type[ReferenceEntity]] = {Vendor, DeviceType}

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200

_TEXT_COLUMNS: dict[type[ReferenceEntity], str] = {
    Vendor: "vendor_name",
    DeviceType: "category_name",
    DeviceSignature: "pattern_value",
}

_ORDER_COLUMNS: dict[type[ReferenceEntity], tuple[str, ...]] = {
    Vendor: ("vendor_name",),
    DeviceType: ("category_name",),
    DeviceSignature: ("evidence_type", "pattern_value"),
}

class DuplicateReferenceError(Exception):
    """Another record already has this name or pattern (DI-07). Nothing was saved.
    existing_id is that other record's id, when known."""

    def __init__(self, message: str, existing_id: int | None = None):
        super().__init__(message)
        self.existing_id = existing_id

class ProtectedReferenceError(Exception):
    """The record is the built-in "unknown" vendor or device type and can't be changed."""

class InactiveReferenceError(Exception):
    """A reference record cannot be activated while a record it needs is retired."""

class EditConflictError(Exception):
    """A reference record changed after the caller loaded it."""

    def __init__(self, record_type: str, record_id: int, latest: AuditLog):
        self.record_type = record_type
        self.record_id = record_id
        self.changed_by = latest.user_id
        self.changed_at = latest.changed_at
        super().__init__(f"{record_type} {record_id} was changed by user {latest.user_id} "
                         f"at {latest.changed_at} after it was loaded")

@dataclass(frozen=True)
class ReferencePage(Generic[T]):
    """One stable page of reference records and the total that match."""

    records: list[T]
    page: int
    per_page: int
    total: int

class ReferenceStore(Generic[T]):
    def __init__(self, engine: DatabaseEngine, entity_type: type[T]):
        if entity_type not in DUPLICATE_KEYS:
            raise TypeError(f"{entity_type.__name__} is not a reference record kind")
        self._engine = engine
        self._type = entity_type
        self._records = Repository(engine, entity_type)
        self._audit = AuditTrail(engine)

    def find(self, pk: int, unit: Any = None) -> T | None:
        return self._records.get(pk, unit)

    def list_page(self, page: int = 1, per_page: int = DEFAULT_PER_PAGE,
                  *, text: str | None = None,
                  status: RecordStatus | str | None = None,
                  unit: Any = None) -> ReferencePage[T]:
        """Return one searchable page, including active and retired records.

        Vendor and device-type names, or a signature's pattern, contain ``text``
        case-insensitively. SQL wildcard characters in the text are matched
        literally. ``status`` may be active, retired or ``"both"``/None.
        """
        _validate_page(page, per_page)
        normalized_status = _normalize_status(status)
        if text is not None and not isinstance(text, str):
            raise ValueError("text must be a string or None")

        conditions: list[str] = []
        params: list[Any] = []
        if text:
            conditions.append(f"`{_TEXT_COLUMNS[self._type]}` LIKE %s ESCAPE '\\\\'")
            params.append(f"%{_escape_like(text)}%")
        if normalized_status is not None:
            conditions.append("`status` = %s")
            params.append(normalized_status.value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        columns = ", ".join(f"`{name}`" for name in self._type.column_names())
        order = ", ".join(f"`{name}`" for name in (*_ORDER_COLUMNS[self._type], self._type.PK))
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(f"SELECT COUNT(*) AS n FROM `{self._type.TABLE}`{where}", tuple(params))
            total = int(cursor.fetchone()["n"])
            cursor.execute(
                f"SELECT {columns} FROM `{self._type.TABLE}`{where} "
                f"ORDER BY {order} LIMIT %s OFFSET %s",
                (*params, per_page, (page - 1) * per_page),
            )
            rows = cursor.fetchall()
        return ReferencePage([self._type.from_row(row) for row in rows], page, per_page, total)

    # ---- 3.2.2 Store Reference Record ---------------------------------------------

    def store(self, record: T, changed_by: int, unit: Any = None,
              *, loaded_at: datetime | None = None) -> T:
        """Create the record (no id yet) or update it, plus its audit entry.

        Raises DuplicateReferenceError if another record has the same name or
        pattern, ProtectedReferenceError for the built-in "unknown" rows, and
        RecordNotFoundError if an update's record doesn't exist. In every case
        nothing is saved.
        """
        with _unit_of_work(self._engine, unit) as cursor:
            self._refuse_if_protected(record.pk)
            if record.pk is None:
                self._reject_duplicate(record, cursor)      # check before writing anything
                old = None
                self._records.create(record, cursor)
                action = AuditAction.INSERT
            else:
                self._lock(record.pk, cursor)
                old = self._records.get(record.pk, cursor)
                if old is None:
                    raise RecordNotFoundError(f"{self._type.TABLE} {record.pk} does not exist")
                if loaded_at is not None:
                    self._check_unchanged_since(record.pk, loaded_at, cursor)
                self._reject_duplicate(record, cursor)
                self._records.update(record, cursor)
                action = AuditAction.UPDATE
            self._audit.record(changed_by, action, old, record, cursor)
        return record

 # ---- 3.2.3 Retire Reference Record ------------------------------------------

    def retire(self, pk: int, changed_by: int, unit: Any = None,
               *, loaded_at: datetime | None = None) -> T:
        """Mark the record retired, plus its audit entry. Never deletes (DI-06).

        Rows that use it (devices, evidence) are not touched, so history stays
        intact; retired records are just left out of new matches (3.2.1).
        Retiring a record that is already retired changes nothing.
        """
        with _unit_of_work(self._engine, unit) as cursor:
            self._lock(pk, cursor)
            record = self._records.get(pk, cursor)
            if record is None:
                raise RecordNotFoundError(f"{self._type.TABLE} {pk} does not exist")
            self._refuse_if_protected(pk)
            if loaded_at is not None:
                self._check_unchanged_since(pk, loaded_at, cursor)
            if not record.is_active:
                return record
            old = self._records.get(pk, cursor)          # a separate copy for the audit's old value
            record.retire()
            self._records.update(record, cursor)
            self._audit.record(changed_by, AuditAction.UPDATE, old, record, cursor)
        return record

    def reactivate(self, pk: int, changed_by: int, unit: Any = None,
                   *, loaded_at: datetime | None = None) -> T:
        """Bring a retired record back, with an audit entry.

        Reactivating an active record changes nothing. A device signature stays
        retired while its device type is retired, so scans cannot use a rule
        whose result is unavailable.
        """
        with _unit_of_work(self._engine, unit) as cursor:
            self._lock(pk, cursor)
            record = self._records.get(pk, cursor)
            if record is None:
                raise RecordNotFoundError(f"{self._type.TABLE} {pk} does not exist")
            if loaded_at is not None:
                self._check_unchanged_since(pk, loaded_at, cursor)
            if record.is_active:
                return record
            if self._type is DeviceSignature:
                device_type = Repository(self._engine, DeviceType).get(record.device_type_id, cursor)
                if device_type is None or not device_type.is_active:
                    raise InactiveReferenceError(
                        f"device signature {pk} cannot be reactivated while device type "
                        f"{record.device_type_id} is retired")
            old = self._records.get(pk, cursor)
            record.reactivate()
            self._records.update(record, cursor)
            self._audit.record(changed_by, AuditAction.UPDATE, old, record, cursor)
        return record

    # ---- 3.2.4 Check Reference In Use -------------------------------------------

    def usage(self, pk: int, unit: Any = None) -> dict[str, int]:
        """How many rows in each table point at this record, e.g. {"device": 3}."""
        counts = {}
        with _unit_of_work(self._engine, unit) as cursor:
            for table, column in USED_BY[self._type]:
                cursor.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE `{column}` = %s", (pk,))
                counts[table] = cursor.fetchone()["n"]
        return counts

    def in_use(self, pk: int, unit: Any = None) -> bool:
        """True if anything points at this record."""
        return any(self.usage(pk, unit).values())

    # ---- helpers ---------------------------------------------------------------

    def _refuse_if_protected(self, pk: int | None) -> None:
        if self._type in PROTECTED and pk == UNKNOWN_ID:
            raise ProtectedReferenceError(
                f"{self._type.TABLE} {UNKNOWN_ID} is the built-in 'unknown' record and can't be changed")

    def _reject_duplicate(self, record: T, cursor: Any) -> None:
        """Look for another record with the same key columns. The comparison uses
        the column's collation, so "ACME" and "Acme" count as the same name."""
        keys = DUPLICATE_KEYS[self._type]
        matches = self._records.list(cursor, **{key: getattr(record, key) for key in keys})
        others = [match for match in matches if match.pk != record.pk]   # an update may keep its own name
        if others:
            shown = ", ".join(f"{key} '{_text(getattr(record, key))}'" for key in keys)
            raise DuplicateReferenceError(
                f"a {self._type.TABLE} with {shown} already exists (id {others[0].pk})",
                existing_id=others[0].pk)

    def _lock(self, pk: int, cursor: Any) -> None:
        cursor.execute(
            f"SELECT `{self._type.PK}` FROM `{self._type.TABLE}` "
            f"WHERE `{self._type.PK}` = %s FOR UPDATE",
            (pk,),
        )
        cursor.fetchall()

    def _check_unchanged_since(self, pk: int, loaded_at: datetime, cursor: Any) -> None:
        latest = self._audit.latest(self._type.TABLE, pk, cursor)
        if latest is not None and latest.changed_at > loaded_at:
            raise EditConflictError(self._type.TABLE, pk, latest)


def _text(value: Any) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def _validate_page(page: int, per_page: int) -> None:
    if (isinstance(page, bool) or not isinstance(page, int) or page < 1 or
            isinstance(per_page, bool) or not isinstance(per_page, int) or
            not 1 <= per_page <= MAX_PER_PAGE):
        raise ValueError(f"page must be 1 or more and per_page 1 to {MAX_PER_PAGE}")


def _normalize_status(status: RecordStatus | str | None) -> RecordStatus | None:
    if status is None or status == "both":
        return None
    try:
        return status if isinstance(status, RecordStatus) else RecordStatus(status)
    except (TypeError, ValueError) as exc:
        raise ValueError("status must be 'active', 'retired' or 'both'") from exc


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
