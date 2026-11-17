"""Fakes for testing 2.3.2 Reference Maintenance without a database.

They copy what the real 3.2 stores do and raise the same errors in the same
order, so the service's handling of each one can be tested:

    clock = FakeClock()
    types = FakeMaintenanceStore(DeviceType, [DeviceType(device_type_id=2, category_name="Phone")], clock)
    types.store(...), types.retire(...), types.reactivate(...), types.usage(...)
    types.changes           # [("update", 2, user_id), ...]: every write, in order
    types.units             # the unit of work each call ran in

    work = FakeUnitOfWork()  # pass `work` where the service wants engine.unit_of_work
    work.committed, work.rolled_back
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.data_access.database_engine import IntegrityConstraintError
from backend.data_access.reference_data import (DuplicateReferenceError, EditConflictError,
                                                InactiveReferenceError, ProtectedReferenceError,
                                                ReferencePage, normalize_prefix)
from backend.data_access.repository import RecordNotFoundError
from backend.records import AuditLog, DeviceSignature, DeviceType, OuiPrefix, Vendor
from backend.records.enums import AuditAction, RecordStatus
from tests.fakes.fake_reference_store import UNKNOWN_ID, FakeReferenceStore

DUPLICATE_KEYS = {
    Vendor: ("vendor_name",),
    DeviceType: ("category_name",),
    DeviceSignature: ("evidence_type", "pattern_value"),
}
PROTECTED = (Vendor, DeviceType)
USED_BY = {
    Vendor: ("device",),
    DeviceType: ("device", "device_signature"),
    DeviceSignature: ("identification_evidence",),
}

TEXT_FIELD = {Vendor: "vendor_name", DeviceType: "category_name", DeviceSignature: "pattern_value"}
ORDER_FIELDS = {
    Vendor: ("vendor_name",),
    DeviceType: ("category_name",),
    DeviceSignature: ("evidence_type", "pattern_value"),
}
MAX_PER_PAGE = 200
SEARCH_MAX_LIMIT = 50

class FakeClock:
    """datetime.now stand-in that moves only when told to."""

    def __init__(self, now: datetime = datetime(2026, 11, 12, 9, 0, 0)) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float = 1) -> None:
        self.now += timedelta(minutes=minutes)


class FakeUnitOfWork:
    """engine.unit_of_work stand-in: counts commits and rollbacks."""

    def __init__(self) -> None:
        self.opened = 0
        self.committed = 0
        self.rolled_back = 0

    @contextmanager
    def __call__(self):
        self.opened += 1
        unit = f"unit-{self.opened}"
        try:
            yield unit
        except Exception:
            self.rolled_back += 1
            raise
        self.committed += 1


class _Audited:
    """The latest change to each record, like the audit log the real stores check."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.changes: list[tuple[str, Any, int]] = []
        self.units: list[Any] = []
        self.latest: dict[Any, tuple[int, datetime]] = {}       # pk -> (user, when)

    def _seen(self, unit: Any) -> None:
        self.units.append(unit)

    def _check_unchanged_since(self, table: str, pk: Any, loaded_at: datetime | None) -> None:
        if loaded_at is None or pk not in self.latest:
            return
        user, when = self.latest[pk]
        if when > loaded_at:
            raise EditConflictError(table, pk, AuditLog(user_id=user, record_type=table, record_id=pk,
                                                        action=AuditAction.UPDATE, changed_at=when))

    def _audit(self, action: str, pk: Any, user: int) -> None:
        self.changes.append((action, pk, user))
        self.latest[pk] = (user, self.clock())

    def changed_elsewhere(self, pk: Any, user: int, when: datetime) -> None:
        """Pretend another user changed the record at `when`."""
        self.latest[pk] = (user, when)


class FakeMaintenanceStore(FakeReferenceStore, _Audited):
    """ReferenceStore for one kind, in memory."""

    def __init__(self, record_type: type, records: Iterable[Any] = (), clock: FakeClock | None = None,
                 *, device_types: FakeMaintenanceStore | None = None) -> None:
        FakeReferenceStore.__init__(self, record_type, records, with_unknown=record_type in PROTECTED)
        _Audited.__init__(self, clock or FakeClock())
        self.record_type = record_type
        self.device_types = device_types        # signatures: for the reactivate check
        self.usage_counts: dict[int, dict[str, int]] = {}
        self.locked: list[int] = []             # ids read with find_for_update
        self.unique_index_refuses_next = False  # act like a second admin's insert won the race
        self.list_calls: list[dict[str, Any]] = []    # every list_page call's arguments
        self._next_id = max(self.rows, default=0) + 1

    def find(self, pk: int, unit: Any = None) -> Any:
        self._seen(unit)
        return super().find(pk, unit)

    def find_for_update(self, pk: int, unit: Any) -> Any:
        self.locked.append(pk)
        return self.find(pk, unit)

    def store(self, record: Any, changed_by: int, unit: Any = None, *, loaded_at: datetime | None = None) -> Any:
        self._seen(unit)
        self._refuse_if_protected(record.pk)
        if self.unique_index_refuses_next:
            self.unique_index_refuses_next = False
            raise IntegrityConstraintError(f"Duplicate entry 'x' for key '{DUPLICATE_KEYS[self.record_type][0]}'")
        if record.pk is None:
            self._reject_duplicate(record)
            setattr(record, record.PK, self._next_id)
            self._next_id += 1
            action = "insert"
        else:
            if record.pk not in self.rows:
                raise RecordNotFoundError(f"{self.record_type.TABLE} {record.pk} does not exist")
            self._check_unchanged_since(self.record_type.TABLE, record.pk, loaded_at)
            self._reject_duplicate(record)
            action = "update"
        self.rows[record.pk] = deepcopy(record)
        self._audit(action, record.pk, changed_by)
        return record

    def retire(self, pk: int, changed_by: int, unit: Any = None, *, loaded_at: datetime | None = None) -> Any:
        return self._set_status(pk, changed_by, unit, loaded_at, retire=True)

    def reactivate(self, pk: int, changed_by: int, unit: Any = None, *,
                   loaded_at: datetime | None = None) -> Any:
        return self._set_status(pk, changed_by, unit, loaded_at, retire=False)

    def usage(self, pk: int, unit: Any = None) -> dict[str, int]:
        self._seen(unit)
        return {table: self.usage_counts.get(pk, {}).get(table, 0) for table in USED_BY[self.record_type]}

    def list_page(self, page: int = 1, per_page: int = 50, *, text: str | None = None,
                  status: RecordStatus | str | None = None, unit: Any = None) -> ReferencePage:
        """ReferenceStore.list_page in memory: the same filters, order and refusals."""
        self._seen(unit)
        self.list_calls.append({"page": page, "per_page": per_page, "text": text, "status": status})
        if page < 1 or not 1 <= per_page <= MAX_PER_PAGE:
            raise ValueError(f"page must be 1 or more and per_page 1 to {MAX_PER_PAGE}")
        wanted = _status(status)

        def matches(row: Any) -> bool:
            if text and text.casefold() not in getattr(row, TEXT_FIELD[self.record_type]).casefold():
                return False
            return wanted is None or row.status == wanted

        def order(row: Any) -> tuple:
            return (*(_fold(getattr(row, name)) for name in ORDER_FIELDS[self.record_type]), row.pk)

        found = sorted((row for row in self.rows.values() if matches(row)), key=order)
        start = (page - 1) * per_page
        return ReferencePage([deepcopy(row) for row in found[start:start + per_page]], page, per_page, len(found))

    def list_active(self, unit: Any = None) -> list[Any]:
        """ReferenceStore.list_active in memory: active records in list order."""
        self._seen(unit)
        return [deepcopy(row) for row in sorted(self.rows.values(), key=self._sort_key) if row.is_active]

    def search(self, text: str, limit: int = 20, unit: Any = None) -> list[Any]:
        """ReferenceStore.search in memory: active, contains the text ignoring case, the
        names that start with it first, at most `limit`. The same refusals."""
        self._seen(unit)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-blank string")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= SEARCH_MAX_LIMIT:
            raise ValueError(f"limit must be 1 to {SEARCH_MAX_LIMIT}")
        needle = text.strip().casefold()

        def name(row: Any) -> str:
            return getattr(row, TEXT_FIELD[self.record_type]).casefold()

        found = [row for row in self.rows.values() if row.is_active and needle in name(row)]
        found.sort(key=lambda row: (not name(row).startswith(needle), *self._sort_key(row)))
        return [deepcopy(row) for row in found[:limit]]

    def _sort_key(self, row: Any) -> tuple:
        """The database's order: by name ignoring case (signatures by evidence type, then pattern), then id."""
        return (*(_fold(getattr(row, name)) for name in ORDER_FIELDS[self.record_type]), row.pk)

    def _set_status(self, pk: int, changed_by: int, unit: Any, loaded_at: datetime | None, *, retire: bool) -> Any:
        self._seen(unit)
        record = self.rows.get(pk)
        if record is None:
            raise RecordNotFoundError(f"{self.record_type.TABLE} {pk} does not exist")
        if retire:
            self._refuse_if_protected(pk)
        self._check_unchanged_since(self.record_type.TABLE, pk, loaded_at)
        if record.is_active == (not retire):
            return deepcopy(record)
        if not retire and self.record_type is DeviceSignature:
            device_type = self.device_types.rows.get(record.device_type_id) if self.device_types else None
            if device_type is None or not device_type.is_active:
                raise InactiveReferenceError(f"device type {record.device_type_id} is retired")
        record.retire() if retire else record.reactivate()
        self._audit("update", pk, changed_by)
        return deepcopy(record)

    def _refuse_if_protected(self, pk: Any) -> None:
        if self.record_type in PROTECTED and pk == UNKNOWN_ID:
            raise ProtectedReferenceError("the built-in 'unknown' record can't be changed")

    def _reject_duplicate(self, record: Any) -> None:
        def key(row: Any) -> tuple:
            return tuple(_fold(getattr(row, name)) for name in DUPLICATE_KEYS[self.record_type])
        for other in self.rows.values():
            if other.pk != record.pk and key(other) == key(record):
                raise DuplicateReferenceError("duplicate", existing_id=other.pk)


class FakePrefixStore(_Audited):
    """OuiPrefixStore in memory."""

    def __init__(self, prefixes: Iterable[OuiPrefix] = (), clock: FakeClock | None = None) -> None:
        super().__init__(clock or FakeClock())
        self.rows: dict[str, OuiPrefix] = {prefix.oui_prefix: deepcopy(prefix) for prefix in prefixes}
        self.unique_index_refuses_next = False
        self._next_id = max((prefix.pk for prefix in self.rows.values()), default=0) + 1

    def find(self, prefix: str, unit: Any = None) -> OuiPrefix | None:
        self._seen(unit)
        found = self.rows.get(normalize_prefix(prefix))
        return deepcopy(found) if found is not None else None

    def for_vendor(self, vendor_id: int, unit: Any = None) -> list[OuiPrefix]:
        """Every prefix one vendor owns, in stored order (the service sorts them)."""
        self._seen(unit)
        return [deepcopy(prefix) for prefix in self.rows.values() if prefix.vendor_id == vendor_id]

    def add(self, prefix: str, vendor_id: int, changed_by: int, unit: Any = None) -> OuiPrefix:
        self._seen(unit)
        normalized = normalize_prefix(prefix)
        if vendor_id == UNKNOWN_ID:
            raise ProtectedReferenceError("the 'unknown' vendor can't own OUI prefixes")
        if normalized in self.rows:
            raise DuplicateReferenceError(f"OUI prefix {normalized} already belongs to a vendor")
        if self.unique_index_refuses_next:
            self.unique_index_refuses_next = False
            raise IntegrityConstraintError(f"Duplicate entry '{normalized}' for key 'oui_prefix'")
        created = OuiPrefix(oui_id=self._next_id, oui_prefix=normalized, vendor_id=vendor_id)
        self._next_id += 1
        self.rows[normalized] = deepcopy(created)
        self._audit("insert", created.pk, changed_by)
        return created

    def reassign(self, prefix: str, vendor_id: int, changed_by: int, unit: Any = None,
                 *, loaded_at: datetime | None = None) -> OuiPrefix:
        self._seen(unit)
        normalized = normalize_prefix(prefix)
        current = self.rows.get(normalized)
        if current is None:
            raise RecordNotFoundError(f"OUI prefix {normalized} does not exist")
        self._check_unchanged_since(OuiPrefix.TABLE, current.pk, loaded_at)
        if vendor_id == UNKNOWN_ID:
            raise ProtectedReferenceError("the 'unknown' vendor can't own OUI prefixes")
        if current.vendor_id != vendor_id:
            current.vendor_id = vendor_id
            self._audit("update", current.pk, changed_by)
        return deepcopy(current)


def _fold(value: Any) -> Any:
    """Compare like the database's collation: "ACME" and "acme" are the same name."""
    text = getattr(value, "value", value)
    return text.casefold() if isinstance(text, str) else text

def _status(status: RecordStatus | str | None) -> RecordStatus | None:
    """Like the real store: 'active', 'retired', or 'both' / None for every record."""
    if status is None or status == "both":
        return None
    try:
        return RecordStatus(status)
    except ValueError as exc:
        raise ValueError("status must be 'active', 'retired' or 'both'") from exc