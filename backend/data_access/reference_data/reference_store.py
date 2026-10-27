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
from enum import Enum
from typing import Any, Generator, Generic, TypeVar

from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import DeviceSignature, DeviceType, Vendor
from backend.records.enums import AuditAction
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

class DuplicateReferenceError(Exception):
    """Another record already has this name or pattern (DI-07). Nothing was saved."""

class ProtectedReferenceError(Exception):
    """The record is the built-in "unknown" vendor or device type and can't be changed."""

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

    # ---- 3.2.2 Store Reference Record ---------------------------------------------

    def store(self, record: T, changed_by: int, unit: Any = None) -> T:
        """Create the record (no id yet) or update it, plus its audit entry.

        Raises DuplicateReferenceError if another record has the same name or
        pattern, ProtectedReferenceError for the built-in "unknown" rows, and
        RecordNotFoundError if an update's record doesn't exist. In every case
        nothing is saved.
        """
        with _unit_of_work(self._engine, unit) as cursor:
            self._refuse_if_protected(record.pk)
            self._reject_duplicate(record, cursor)          # check before writing anything
            if record.pk is None:
                old = None
                self._records.create(record, cursor)
                action = AuditAction.INSERT
            else:
                old = self._records.get(record.pk, cursor)
                if old is None:
                    raise RecordNotFoundError(f"{self._type.TABLE} {record.pk} does not exist")
                self._records.update(record, cursor)
                action = AuditAction.UPDATE
            self._audit.record(changed_by, action, old, record, cursor)
        return record

 # ---- 3.2.3 Retire Reference Record ------------------------------------------

    def retire(self, pk: int, changed_by: int, unit: Any = None) -> T:
        """Mark the record retired, plus its audit entry. Never deletes (DI-06).

        Rows that use it (devices, evidence) are not touched, so history stays
        intact; retired records are just left out of new matches (3.2.1).
        Retiring a record that is already retired changes nothing.
        """
        with _unit_of_work(self._engine, unit) as cursor:
            record = self._records.get(pk, cursor)
            if record is None:
                raise RecordNotFoundError(f"{self._type.TABLE} {pk} does not exist")
            self._refuse_if_protected(pk)
            if not record.is_active:
                return record
            old = self._records.get(pk, cursor)          # a separate copy for the audit's old value
            record.retire()
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
                f"a {self._type.TABLE} with {shown} already exists (id {others[0].pk})")


def _text(value: Any) -> str:
    return value.value if isinstance(value, Enum) else str(value)


@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
