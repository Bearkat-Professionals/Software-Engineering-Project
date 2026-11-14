"""2.3.2 Reference Maintenance: the changes an admin makes to reference data.

2.3.2.1 add, 2.3.2.2 edit and 2.3.2.3 retire or reactivate a vendor, device type
or device signature, plus adding or moving an OUI prefix. The kind travels as data
(design 8.2), so one set of operations covers all three kinds.

Every operation checks its values with SH-1, runs in ONE unit of work, and returns
one MaintenanceResult the routes turn into JSON. Every problem with the values is
reported at once (OUT-04), and nothing is stored unless all of them pass.

    maintenance = ReferenceMaintenance(
        {ReferenceKind.VENDOR: ReferenceStore(engine, Vendor),
         ReferenceKind.DEVICE_TYPE: ReferenceStore(engine, DeviceType),
         ReferenceKind.SIGNATURE: ReferenceStore(engine, DeviceSignature)},
        OuiPrefixStore(engine), engine.unit_of_work)

    result = maintenance.add(ReferenceKind.VENDOR, {"vendor_name": "Acme"}, acting_user_id=1)
    result.status       # MaintenanceStatus.DONE, REJECTED, NOT_FOUND, DUPLICATE, PROTECTED or CONFLICT
    result.reasons      # sentences a person can read, when it wasn't DONE

Names and patterns are stored as typed (trimmed), so a screen that shows them must
escape them (XSS).
"""

from __future__ import annotations
from backend.data_access.database_engine import IntegrityConstraintError
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from backend.data_access.reference_data import (DuplicateReferenceError, EditConflictError,
                                                InactiveReferenceError, InvalidPrefixError,
                                                ProtectedReferenceError, normalize_prefix)
from backend.data_access.repository import RecordNotFoundError
from backend.records import DeviceSignature, DeviceType, OuiPrefix, Vendor
from backend.records.enums import EvidenceType
from backend.records.reference_entity import ReferenceEntity
from backend.shared_modules.validate_input import validate_reference_name, validate_signature_pattern


RESULT_TEXT_MAX = 255
DUPLICATE_KEY = 1062            # MySQL's error number for a duplicate UNIQUE value

# OUI evidence is matched against oui_prefix (match_oui), never against signatures,
# so a signature can't use it: it would be a rule that never matches anything.
SIGNATURE_EVIDENCE = tuple(kind for kind in EvidenceType if kind is not EvidenceType.OUI)
EVIDENCE_TYPES = ", ".join(kind.value for kind in SIGNATURE_EVIDENCE)
_EVIDENCE_WORDS = ", ".join(kind.value for kind in SIGNATURE_EVIDENCE[:-1]) + " or " + SIGNATURE_EVIDENCE[-1].value

#the three kinds
@dataclass(frozen=True)
class _KindInfo:
    record_class: type[ReferenceEntity]
    name_field: str             
    max_length: int             
    label: str               

class ReferenceKind(str, Enum):
    VENDOR = "vendor"
    DEVICE_TYPE = "device_type"
    SIGNATURE = "signature"  

    @property
    def record_class(self) -> type[ReferenceEntity]:
        return _KINDS[self].record_class

    @property
    def name_field(self) -> str:
        return _KINDS[self].name_field

    @property
    def max_length(self) -> int:
        return _KINDS[self].max_length

    @property
    def label(self) -> str:
        return _KINDS[self].label

_KINDS: dict[ReferenceKind, _KindInfo] = {
    ReferenceKind.VENDOR: _KindInfo(Vendor, "vendor_name", 255, "vendor"),
    ReferenceKind.DEVICE_TYPE: _KindInfo(DeviceType, "category_name", 100, "device type"),
    ReferenceKind.SIGNATURE: _KindInfo(DeviceSignature, "pattern_value", 255, "signature"),
}

_EDITABLE: dict[ReferenceKind, tuple[str, ...]] = {
    ReferenceKind.VENDOR: ("vendor_name",),
    ReferenceKind.DEVICE_TYPE: ("category_name",),
    ReferenceKind.SIGNATURE: ("pattern_value", "evidence_type", "device_type_id",
                              "resulting_device_name", "resulting_model"),
}

_REQUIRED_TO_ADD: dict[ReferenceKind, tuple[str, ...]] = {
    ReferenceKind.VENDOR: ("vendor_name",),
    ReferenceKind.DEVICE_TYPE: ("category_name",),
    ReferenceKind.SIGNATURE: ("pattern_value", "evidence_type", "device_type_id"),
}

_NEEDS: dict[str, str] = {
    "vendor_name": "a name", "category_name": "a name", "pattern_value": "a pattern",
    "evidence_type": "an evidence type", "device_type_id": "a device type",
}

_USED_BY_WORDS: dict[str, tuple[str, str]] = {
    "device": ("device", "devices"),
    "device_signature": ("signature", "signatures"),
    "identification_evidence": ("device identification", "device identifications"),
}

#the result
class MaintenanceStatus(str, Enum):
    DONE = "done"
    REJECTED = "rejected"      
    NOT_FOUND = "not_found"
    DUPLICATE = "duplicate"     
    PROTECTED = "protected"     
    CONFLICT = "conflict" 

@dataclass(frozen = True)
class MaintenanceResult:
    """What one change did. Shared by the maintenance services, so the routes turn
    every one into JSON the same way."""
    status: MaintenanceStatus
    record: Any = None                      
    reasons: tuple[str, ...] = ()           
    changed_by: int | None = None           
    changed_at: datetime | None = None      
    existing_id: int | None = None          

    @property
    def done(self) -> bool:
        return self.status is MaintenanceStatus.DONE

@dataclass(frozen=True)
class ReferenceUsage:
    """For the retire confirmation: what uses the record, in numbers and in words."""
    status: MaintenanceStatus               
    counts: Mapping[str, int]
    sentence: str

#what the service needs from 3.2
class ReferenceRecords(Protocol):
    """ReferenceStore(engine, kind) is one; tests pass a fake."""

    def find(self, pk: int, unit: Any = None) -> ReferenceEntity | None: ...
    def find_for_update(self, pk: int, unit: Any) -> ReferenceEntity | None: ...
    def store(self, record: Any, changed_by: int, unit: Any = None,
              *, loaded_at: datetime | None = None) -> Any: ...
    def retire(self, pk: int, changed_by: int, unit: Any = None,
               *, loaded_at: datetime | None = None) -> Any: ...
    def reactivate(self, pk: int, changed_by: int, unit: Any = None,
                   *, loaded_at: datetime | None = None) -> Any: ...
    def usage(self, pk: int, unit: Any = None) -> dict[str, int]: ...

class PrefixRecords(Protocol):
    """OuiPrefixStore(engine) is one."""

    def find(self, prefix: str, unit: Any = None) -> OuiPrefix | None: ...
    def add(self, prefix: str, vendor_id: int, changed_by: int, unit: Any = None) -> OuiPrefix: ...
    def reassign(self, prefix: str, vendor_id: int, changed_by: int, unit: Any = None,
                 *, loaded_at: datetime | None = None) -> OuiPrefix: ...



#the service 

class ReferenceMaintenance:
    def __init__(self, stores: Mapping[ReferenceKind, ReferenceRecords], prefixes: PrefixRecords,
                 unit_of_work: Callable[[], AbstractContextManager[Any]],
                 clock: Callable[[], datetime] = datetime.now) -> None:
        missing = set(ReferenceKind) - set(stores)
        if missing:
            raise ValueError(f"no store for {sorted(kind.value for kind in missing)}")
        self._stores = dict(stores)
        self._prefixes = prefixes
        self._unit_of_work = unit_of_work
        self._clock = clock

#add

    def add(self, kind: ReferenceKind, values: Mapping[str, Any], acting_user_id: int) -> MaintenanceResult:
        kind = ReferenceKind(kind)
        reasons, cleaned = _check_values(kind, values, adding=True)
        if reasons:
            return _rejected(reasons)
        record = kind.record_class(**cleaned)
        return self._run(kind, None, lambda unit: self._store(kind, record, None, acting_user_id, None, unit))

#edit 

    def edit(self, kind: ReferenceKind, record_id: int, values: Mapping[str, Any], acting_user_id: int,
             loaded_at: datetime | None) -> MaintenanceResult:
        """Change only the values sent; the rest stay as stored."""
        kind = ReferenceKind(kind)
        reasons, cleaned = _check_values(kind, values, adding=False)
        if reasons:
            return _rejected(reasons)

        def change(unit: Any) -> MaintenanceResult:
            stored = self._stores[kind].find_for_update(record_id, unit)
            if stored is None:
                return _not_found(kind, record_id)
            changed = replace(stored, **cleaned)
            if changed == stored:                               # nothing to change: no audit entry
                return MaintenanceResult(MaintenanceStatus.DONE, stored)
            return self._store(kind, changed, stored, acting_user_id, loaded_at, unit)

        return self._run(kind, record_id, change)

#retire and reactivate

    def retire(self, kind: ReferenceKind, record_id: int, acting_user_id: int,
               loaded_at: datetime | None) -> MaintenanceResult:
        """Retired records stay on the devices that use them, but match nothing new (DI-06)."""
        kind = ReferenceKind(kind)
        store = self._stores[kind]
        return self._run(kind, record_id, lambda unit: self._done(
            store.retire(record_id, acting_user_id, unit, loaded_at=loaded_at), acting_user_id))

    def reactivate(self, kind: ReferenceKind, record_id: int, acting_user_id: int,
                   loaded_at: datetime | None) -> MaintenanceResult:
        kind = ReferenceKind(kind)
        store = self._stores[kind]
        return self._run(kind, record_id, lambda unit: self._done(
            store.reactivate(record_id, acting_user_id, unit, loaded_at=loaded_at), acting_user_id))

    def usage(self, kind: ReferenceKind, record_id: int) -> ReferenceUsage:
        """'12 devices and 3 signatures use this device type', for the retire confirmation."""
        kind = ReferenceKind(kind)
        store = self._stores[kind]
        with self._unit_of_work() as unit:
            if store.find(record_id, unit) is None:
                return ReferenceUsage(MaintenanceStatus.NOT_FOUND, {}, _not_found(kind, record_id).reasons[0])
            counts = store.usage(record_id, unit)
        return ReferenceUsage(MaintenanceStatus.DONE, counts, usage_sentence(kind, counts))

#OUI prefixes 

    def add_prefix(self, vendor_id: int, prefix: str, acting_user_id: int) -> MaintenanceResult:
        reasons, normalized = _check_prefix(prefix, vendor_id)
        if reasons:
            return _rejected(reasons)

        def change(unit: Any) -> MaintenanceResult:
            refused = self._refuse_vendor(vendor_id, unit)
            if refused is not None:
                return refused
            existing = self._prefixes.find(normalized, unit)
            if existing is not None:
                owner = self._stores[ReferenceKind.VENDOR].find(existing.vendor_id, unit)
                return _prefix_taken(normalized, existing, owner)
            return self._done(self._prefixes.add(normalized, vendor_id, acting_user_id, unit), acting_user_id)

        return self._run_prefix(normalized, change)

    def move_prefix(self, prefix: str, vendor_id: int, acting_user_id: int,
                    loaded_at: datetime | None) -> MaintenanceResult:
        """Give a stored prefix to another vendor (a correction)."""
        reasons, normalized = _check_prefix(prefix, vendor_id)
        if reasons:
            return _rejected(reasons)

        def change(unit: Any) -> MaintenanceResult:
            refused = self._refuse_vendor(vendor_id, unit)
            if refused is not None:
                return refused
            moved = self._prefixes.reassign(normalized, vendor_id, acting_user_id, unit, loaded_at=loaded_at)
            return self._done(moved, acting_user_id)

        return self._run_prefix(normalized, change)

#helpers
    def _store(self, kind: ReferenceKind, record: Any, stored: Any, acting_user_id: int,
               loaded_at: datetime | None, unit: Any) -> MaintenanceResult:
        """The checks that need the database, then the store. Runs inside the unit of work."""
        if kind is ReferenceKind.SIGNATURE:
            changing_type = stored is None or record.device_type_id != stored.device_type_id
            if changing_type:
                refused = self._refuse_device_type(record.device_type_id, unit)
                if refused is not None:
                    return refused
        saved = self._stores[kind].store(record, acting_user_id, unit, loaded_at=loaded_at)
        return self._done(saved, acting_user_id)

    def _refuse_device_type(self, device_type_id: int, unit: Any) -> MaintenanceResult | None:
        """A signature's device type must exist and be active (DI-06)."""
        device_type = self._stores[ReferenceKind.DEVICE_TYPE].find_for_update(device_type_id, unit)
        if device_type is None:
            return _rejected([f"There is no device type with id {device_type_id}."])
        if not device_type.is_active:
            return _rejected([f"The device type '{device_type.category_name}' is retired, so a signature "
                              "can't point at it. Reactivate it first, or pick another type."])
        return None

    def _refuse_vendor(self, vendor_id: int, unit: Any) -> MaintenanceResult | None:
        """A prefix's vendor must exist and be active. The store refuses the "unknown" one."""
        vendor = self._stores[ReferenceKind.VENDOR].find_for_update(vendor_id, unit)
        if vendor is None:
            return _not_found(ReferenceKind.VENDOR, vendor_id)
        if not vendor.is_active:
            return _rejected([f"The vendor '{vendor.vendor_name}' is retired, so it can't be given "
                              "OUI prefixes. Reactivate it first."])
        return None

    def _done(self, record: Any, acting_user_id: int) -> MaintenanceResult:
        # Read after the store wrote its audit entry, so it is never earlier than that entry:
        # a screen that sends it back as loaded_at won't conflict with its own change.
        return MaintenanceResult(MaintenanceStatus.DONE, record, changed_by=acting_user_id,
                                 changed_at=self._clock())

    def _run(self, kind: ReferenceKind, record_id: int | None,
             change: Callable[[Any], MaintenanceResult]) -> MaintenanceResult:
        """Run one change in one unit of work. The unit rolls back BEFORE an error from 3.2
        is turned into a result, so a refused change leaves nothing behind."""
        try:
            with self._unit_of_work() as unit:
                return change(unit)
        except DuplicateReferenceError as exc:
            return _duplicate(kind, exc)
        except ProtectedReferenceError:
            return MaintenanceResult(MaintenanceStatus.PROTECTED, reasons=(
                f"The built-in 'Unknown' {kind.label} can't be changed or retired: "
                "new devices use it until they are identified.",))
        except RecordNotFoundError:
            return _not_found(kind, record_id)
        except EditConflictError as exc:
            return _conflict(kind.label, exc)
        except InactiveReferenceError:
            return _rejected(["This signature's device type is retired. Reactivate the device type first."])
        except IntegrityConstraintError as exc:
            if not _is_duplicate_key(exc):
                raise                                    
            return _duplicate(kind, exc)

    def _run_prefix(self, prefix: str, change: Callable[[Any], MaintenanceResult]) -> MaintenanceResult:
        try:
            with self._unit_of_work() as unit:
                return change(unit)
        except DuplicateReferenceError:
            return _prefix_race(prefix)
        except IntegrityConstraintError as exc:             # added by someone else at the same moment
            if not _is_duplicate_key(exc):
                raise
            return _prefix_race(prefix)
        except ProtectedReferenceError:
            return MaintenanceResult(MaintenanceStatus.PROTECTED, reasons=(
                "The built-in 'Unknown' vendor can't own OUI prefixes.",))
        except RecordNotFoundError:
            return MaintenanceResult(MaintenanceStatus.NOT_FOUND, reasons=(
                f"OUI prefix {prefix} isn't stored yet. Add it instead.",))
        except EditConflictError as exc:
            return _conflict("OUI prefix", exc)

def usage_sentence(kind: ReferenceKind, counts: Mapping[str, int]) -> str:
    """{"device": 12, "device_signature": 3} -> '12 devices and 3 signatures use this device type.'"""
    kind = ReferenceKind(kind)
    parts = []
    for table, count in counts.items():
        if count:
            one, many = _USED_BY_WORDS.get(table, (table.replace("_", " "), table.replace("_", " ")))
            parts.append(f"{count} {one if count == 1 else many}")
    if not parts:
        return f"Nothing uses this {kind.label}."
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    verb = "uses" if len(parts) == 1 and listed.startswith("1 ") else "use"
    return f"{listed} {verb} this {kind.label}."

#checking values
def _check_values(kind: ReferenceKind, values: Any, *, adding: bool) -> tuple[list[str], dict[str, Any]]:
    """Every problem with the values (OUT-04), and the cleaned values to store."""
    if not isinstance(values, Mapping):
        return ["Send the values as a JSON object."], {}
    reasons: list[str] = []
    cleaned: dict[str, Any] = {}

    allowed = _EDITABLE[kind]
    for name in values:
        if name not in allowed:
            reasons.append(f"'{name}' can't be set on a {kind.label}. "
                           f"These can: {', '.join(allowed)}.")
    if adding:
        for name in _REQUIRED_TO_ADD[kind]:
            if values.get(name) is None:
                reasons.append(f"A new {kind.label} needs {_NEEDS[name]}.")
    elif not any(name in allowed for name in values):
        reasons.append(f"Nothing to change. Send at least one of: {', '.join(allowed)}.")

    for name, value in values.items():
        if name not in allowed:
            continue
        if adding and value is None and name in _REQUIRED_TO_ADD[kind]:
            continue                                        
        reason, value = _check_one(kind, name, value)
        if reason:
            reasons.append(reason)
        else:
            cleaned[name] = value
    return reasons, cleaned

def _check_one(kind: ReferenceKind, name: str, value: Any) -> tuple[str | None, Any]:
    if name == kind.name_field:
        if kind is ReferenceKind.SIGNATURE:
            result = validate_signature_pattern(value)
        else:
            result = validate_reference_name(value, kind.max_length)
        return (None, result.value) if result else (result.reason, None)

    if name == "evidence_type":
        try:
            evidence = EvidenceType(value.strip().lower() if isinstance(value, str) else value)
        except ValueError:
            return f"'{value}' is not an evidence type. Use one of: {EVIDENCE_TYPES}.", None
        if evidence not in SIGNATURE_EVIDENCE:
            return (f"Signatures match {_EVIDENCE_WORDS} evidence. "
                    "OUI prefixes are added to a vendor instead."), None
        return None, evidence

    if name == "device_type_id":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return f"The device type id must be a whole number, not '{value}'.", None
        return None, value

    label = name.replace("_", " ")
    if value is None or isinstance(value, str) and not value.strip():
        return None, None
    if not isinstance(value, str):
        return f"The {label} must be text.", None
    result = validate_reference_name(value, RESULT_TEXT_MAX)     
    if not result:
        return f"The {label} " + result.reason.removeprefix("The name "), None
    return None, result.value

def _check_prefix(prefix: Any, vendor_id: Any) -> tuple[list[str], str | None]:
    reasons: list[str] = []
    normalized = None
    try:
        normalized = normalize_prefix(prefix if isinstance(prefix, str) else "")
    except InvalidPrefixError:
        shown = prefix.strip() if isinstance(prefix, str) and prefix.strip() else "(blank)"
        reasons.append(f"'{shown}' is not an OUI prefix. Use 6 hex digits, like 28:6F:B9.")
    if isinstance(vendor_id, bool) or not isinstance(vendor_id, int) or vendor_id < 1:
        reasons.append(f"The vendor id must be a whole number, not '{vendor_id}'.")
    return reasons, normalized

#results 
def _rejected(reasons: list[str]) -> MaintenanceResult:
    return MaintenanceResult(MaintenanceStatus.REJECTED, reasons=tuple(reasons))

def _not_found(kind: ReferenceKind, record_id: Any) -> MaintenanceResult:
    return MaintenanceResult(MaintenanceStatus.NOT_FOUND, reasons=(
        f"There is no {kind.label} with id {record_id}. It may have been removed; reload the list.",))

def _duplicate(kind: ReferenceKind, exc: Exception) -> MaintenanceResult:
    if kind is ReferenceKind.SIGNATURE:
        reason = "A signature with this evidence type and pattern already exists."
    else:
        reason = f"A {kind.label} with this name already exists."
    return MaintenanceResult(MaintenanceStatus.DUPLICATE, reasons=(reason,),
                             existing_id=getattr(exc, "existing_id", None))

def _prefix_taken(prefix: str, existing: OuiPrefix, owner: Vendor | None) -> MaintenanceResult:
    who = owner.vendor_name if owner is not None else "another vendor"
    return MaintenanceResult(MaintenanceStatus.DUPLICATE, existing_id=existing.pk, reasons=(
        f"OUI prefix {prefix} already belongs to {who}. Move it instead.",))


def _prefix_race(prefix: str) -> MaintenanceResult:
    return MaintenanceResult(MaintenanceStatus.DUPLICATE, reasons=(
        f"OUI prefix {prefix} was just added by someone else. Reload, then move it if needed.",))


def _is_duplicate_key(exc: IntegrityConstraintError) -> bool:
    """True when the UNIQUE index refused a duplicate. The engine raises the same error for a
    missing foreign key or an empty required column, and those are faults, not duplicates."""
    cause = exc.__cause__
    return getattr(cause, "errno", None) == DUPLICATE_KEY or "Duplicate entry" in str(exc)

def _conflict(label: str, exc: EditConflictError) -> MaintenanceResult:
    when = exc.changed_at.strftime("%H:%M") if exc.changed_at else "just now"
    return MaintenanceResult(MaintenanceStatus.CONFLICT, changed_by=exc.changed_by, changed_at=exc.changed_at,
                             reasons=(f"Someone else changed this {label} at {when}, after you opened it. "
                                      "Reload it to see their change, then try again.",))