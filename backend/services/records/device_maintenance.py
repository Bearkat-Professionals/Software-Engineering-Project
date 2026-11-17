"""2.3.1 Device Record Maintenance: the changes a user makes to a device by hand.

2.3.1.1 add, 2.3.1.2 edit and 2.3.1.4 mark trusted / flagged / automatic go through
SH-4 Save Device Record, the same path scans use, so a device is checked, flagged
and audited the same way however it changes. 2.3.1.3 delete goes through 3.1.3.
Services never call each other (OOD), so this uses SH-4 and the stores, not the
Scan Manager.

Every method runs in ONE unit of work, so a failure leaves nothing half-saved
(CM-04), and returns one DeviceMaintenanceResult the routes turn into JSON.

    maintenance = DeviceMaintenance(engine, saver, DeviceStore(engine))

    form = maintenance.load(7)                          # the device, and when it was read
    result = maintenance.edit(7, DeviceEdit(device_name="Den TV"), acting_user_id=1,
                              loaded_at=form.loaded_at)
    result.status       # DONE, REJECTED, NOT_FOUND, CONFLICT or ALREADY_EXISTS
    result.reasons      # sentences a person can read, when it wasn't DONE

Flagging uses the same level rule as scans (stored_level, next to assign_level), worked
out from the device as it will be after the change. Typing a name and a type doesn't
raise the confidence score (only evidence does), so a device under 50 stays partial and
flagged on "automatic": mark it Trusted to clear that. This is on purpose, not a bug.

Times (loaded_at, changed_at) come from the same clock the audit log uses, so the
conflict check compares like with like.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from backend.data_access.database_engine import IntegrityConstraintError
from backend.data_access.network_data import DeviceListing, EditConflictError
from backend.data_access.repository import RecordNotFoundError
from backend.records import Device, User
from backend.records.enums import IdentificationLevel
from backend.services.scan.assign_level import stored_level
from backend.services.scan.detect_randomized_mac import is_randomized_mac
from backend.shared_modules.save_device_record import (DeviceChanges, DeviceRecordSaver, SaveOutcome,
                                                       SaveStatus)
from backend.shared_modules.validate_input import validate_mac


MARKS: dict[str, tuple[bool, bool]] = {
    "trusted": (True, False),
    "flagged": (False, True),
    "automatic": (False, False),
}


class DeviceMaintenanceStatus(str, Enum):
    DONE = "done"
    REJECTED = "rejected"              
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"               
    ALREADY_EXISTS = "already_exists"   


@dataclass(frozen=True)
class DeviceMaintenanceResult:
    status: DeviceMaintenanceStatus
    device: Device | None = None                
    reasons: tuple[str, ...] = ()               
    changed_by: int | None = None               
    changed_at: datetime | None = None          
    existing_device_id: int | None = None       
    removed: dict[str, int] | None = None       

    @property
    def done(self) -> bool:
        return self.status is DeviceMaintenanceStatus.DONE


@dataclass(frozen=True)
class DeviceEdit:
    """What a user can change by hand. None means "leave as it is"; an empty
    device_name or model means "clear it" (SH-4)."""
    device_name: str | None = None
    device_type_id: int | None = None
    vendor_id: int | None = None
    model: str | None = None

    @property
    def empty(self) -> bool:
        return all(value is None for value in (self.device_name, self.device_type_id, self.vendor_id, self.model))


@dataclass(frozen=True)
class LoadedDevice:
    """load()'s answer for the edit form: the device with its vendor and type names, and
    when it was read (on the audit log's clock)."""
    device: Device | None
    loaded_at: datetime | None
    vendor_name: str | None = None
    device_type_name: str | None = None

    @property
    def found(self) -> bool:
        return self.device is not None


class UnitOfWork(Protocol):
    """DatabaseEngine is one; tests pass a fake."""

    def unit_of_work(self) -> AbstractContextManager[Any]: ...


class UserFinder(Protocol):
    """3.3.1 Find User, to name who caused a conflict. UserStore is one."""

    def find(self, user_id: int, unit: Any = None) -> User | None: ...


class DeviceRecords(Protocol):
    """The parts of 3.1 DeviceStore this uses."""

    def find(self, device_id: int, unit: Any = None, *, lock: bool = False) -> Device | None: ...
    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None, *, lock: bool = False) -> Device | None: ...
    def delete(self, device_id: int, changed_by: int, unit: Any = None,
               *, loaded_at: datetime | None = None) -> dict[str, int]: ...
    def find_listing(self, device_id: int, unit: Any = None) -> DeviceListing | None: ...

class DeviceMaintenance:
    def __init__(self, engine: UnitOfWork, saver: DeviceRecordSaver, devices: DeviceRecords,
                 users: UserFinder | None = None, clock: Callable[[], datetime] = datetime.now) -> None:
        """users names who caused a delete conflict (without it, "another user"). clock must be
        the audit log's clock (AuditTrail uses datetime.now), or the conflict check is wrong."""
        self._engine = engine
        self._saver = saver
        self._devices = devices
        self._users = users
        self._clock = clock

    #add

    def add(self, mac: str, edit: DeviceEdit, acting_user_id: int, *,
            ip: str | None = None, trusted: bool = False) -> DeviceMaintenanceResult:
        """A device added by hand. It hasn't been seen by a scan yet, so it starts
        offline with no first or last seen. If someone else adds the same MAC at the same
        moment, the second add gets ALREADY_EXISTS, not an error."""
        try:
            with self._engine.unit_of_work() as unit:
                known = self._known(mac, unit)
                if known is not None:
                    return _already_exists(known, just_added=False)
                changes = DeviceChanges(mac_address=mac, current_ip=ip,         # no seen_at: not a sighting
                                        device_name=edit.device_name, device_type_id=edit.device_type_id,
                                        vendor_id=edit.vendor_id, model=edit.model,
                                        trusted_status=True if trusted else None)
                level = stored_level(edit.device_type_id, edit.vendor_id, edit.device_name, 0)   # no evidence yet
                outcome = self._saver.save(changes, acting_user_id, level, unit)
                return self._result(outcome, acting_user_id, unit)
        except IntegrityConstraintError:
            # Another add of the same fixed MAC committed between our check and our insert, and
            # the unique index refused ours; the unit has rolled back. Look again in a fresh unit
            # (it sees their commit): if the device is there, it was a duplicate. If not, it was
            # some other fault, so don't hide it.
            with self._engine.unit_of_work() as unit:
                known = self._known(mac, unit)
            if known is None:
                raise
            return _already_exists(known, just_added=True)

    #edit
    def edit(self, device_id: int, edit: DeviceEdit, acting_user_id: int,
             loaded_at: datetime | None) -> DeviceMaintenanceResult:
        if edit.empty:
            return _rejected(["Nothing to change. Send a name, type, vendor or model."])
        with self._engine.unit_of_work() as unit:
            device, refused = self._find(device_id, unit, lock=True)
            if refused is not None:
                return refused
            changes = DeviceChanges(mac_address=device.mac_address,
                                    device_name=edit.device_name, device_type_id=edit.device_type_id,
                                    vendor_id=edit.vendor_id, model=edit.model)
            outcome = self._saver.save(changes, acting_user_id, _level_after(device, edit), unit, loaded_at)
            return self._result(outcome, acting_user_id, unit)

  
#trusted / flagged / automatic 
    def set_mark(self, device_id: int, mark: str, acting_user_id: int,
                 loaded_at: datetime | None) -> DeviceMaintenanceResult:
        if mark not in MARKS:
            return _rejected([f"'{mark}' is not a mark. Use one of: {', '.join(MARKS)}."])
        trusted, flagged = MARKS[mark]
        with self._engine.unit_of_work() as unit:
            device, refused = self._find(device_id, unit, lock=True)
            if refused is not None:
                return refused
            changes = DeviceChanges(mac_address=device.mac_address,
                                    trusted_status=trusted, flag_set_by_user=flagged)
            outcome = self._saver.save(changes, acting_user_id, _level_after(device, DeviceEdit()),
                                       unit, loaded_at)
            return self._result(outcome, acting_user_id, unit)

#delete 
    def delete(self, device_id: int, acting_user_id: int, loaded_at: datetime | None) -> DeviceMaintenanceResult:
        """Its observations and evidence go with it (DI-05); the counts come back in removed."""
        try:
            with self._engine.unit_of_work() as unit:
                removed = self._devices.delete(device_id, acting_user_id, unit, loaded_at=loaded_at)
                return DeviceMaintenanceResult(DeviceMaintenanceStatus.DONE, changed_by=acting_user_id,
                                               changed_at=self._clock(), removed=dict(removed))
        except RecordNotFoundError:             # the unit has rolled back by the time we get here
            return _not_found(device_id)
        except EditConflictError as conflict:
            with self._engine.unit_of_work() as unit:       # a read only, to name the user
                reason = self._conflict_reason(conflict, loaded_at, unit)
            return DeviceMaintenanceResult(DeviceMaintenanceStatus.CONFLICT, changed_by=conflict.changed_by,
                                           changed_at=conflict.changed_at, reasons=(reason,))

#for the edit form

    def load(self, device_id: int) -> LoadedDevice:
        """The device and when it was read. Send loaded_at back with the edit so a change
        made in between is caught.

        The time is read BEFORE the device: the read fixes what this unit of work can see,
        so a change committed after it but stamped earlier than a later loaded_at would be
        missing from the form and still pass the conflict check."""
        loaded_at = self._clock()
        with self._engine.unit_of_work() as unit:
            listing = self._devices.find_listing(device_id, unit)
        if listing is None:
            return LoadedDevice(None, None)
        return LoadedDevice(listing.device, loaded_at, listing.vendor_name, listing.device_type_name) 

    
#helpers 

    def _known(self, mac: str, unit: Any) -> Device | None:
        """The stored device with this MAC, fixed and randomized looked up separately (DI-02).
        None for a MAC that isn't valid: SH-4 rejects that with its reason."""
        checked = validate_mac(mac)
        if not checked:
            return None
        return self._devices.find_by_mac(checked.value, is_randomized_mac(checked.value), unit)

    def _find(self, device_id: int, unit: Any, *,
              lock: bool = False) -> tuple[Device | None, DeviceMaintenanceResult | None]:
        """The device by id, or the result to return instead.

        SH-4 saves by MAC. Several devices can share one randomized MAC (DI-02), and
        SH-4 would then change the newest one, which may not be this one. Refuse that
        rather than change the wrong device."""
        device = self._devices.find(device_id, unit, lock=lock)
        if device is None:
            return None, _not_found(device_id)
        by_mac = self._devices.find_by_mac(device.mac_address, device.is_randomized_mac, unit, lock=lock)
        if by_mac is None or by_mac.pk != device.pk:
            return None, _rejected([f"Another device shares this randomized MAC ({device.mac_address}), "
                                    "so this one can't be changed by hand yet."])
        return device, None

    def _result(self, outcome: SaveOutcome, acting_user_id: int, unit: Any) -> DeviceMaintenanceResult:
        """SH-4's outcome as this service's result."""
        if outcome.status in (SaveStatus.CREATED, SaveStatus.UPDATED):
            # Read after SH-4 wrote its audit entry, on the same clock, so it is never earlier
            # than that entry: sent back as loaded_at, it won't conflict with your own change.
            return DeviceMaintenanceResult(DeviceMaintenanceStatus.DONE, outcome.device,
                                           changed_by=acting_user_id, changed_at=self._clock())
        if outcome.status is SaveStatus.CONFLICT:
            return DeviceMaintenanceResult(DeviceMaintenanceStatus.CONFLICT, reasons=outcome.reasons,
                                           changed_by=outcome.changed_by, changed_at=outcome.changed_at)
        return DeviceMaintenanceResult(DeviceMaintenanceStatus.REJECTED, reasons=outcome.reasons)

    def _conflict_reason(self, conflict: EditConflictError, loaded_at: datetime, unit: Any) -> str:
        """Worded exactly like SH-4's edit conflict, so both read the same: who, and when
        (with the date if it wasn't the day the form was opened)."""
        user = self._users.find(conflict.changed_by, unit) if self._users and conflict.changed_by else None
        who = user.username if user is not None else "another user"
        at = conflict.changed_at
        when = f"{at:%H:%M}" if at.date() == loaded_at.date() else f"{at:%Y-%m-%d %H:%M}"
        return f"This device was changed by {who} at {when} after you opened it."


def _level_after(device: Device, edit: DeviceEdit) -> IdentificationLevel:
    """The level of the device as it will be once the edit is applied. A blank name clears it."""
    def pick(new: Any, current: Any) -> Any:
        return current if new is None else new
    return stored_level(pick(edit.device_type_id, device.device_type_id),
                        pick(edit.vendor_id, device.vendor_id),
                        pick(edit.device_name, device.device_name),
                        device.confidence_score)


def _rejected(reasons: list[str]) -> DeviceMaintenanceResult:
    return DeviceMaintenanceResult(DeviceMaintenanceStatus.REJECTED, reasons=tuple(reasons))


def _not_found(device_id: int) -> DeviceMaintenanceResult:
    return DeviceMaintenanceResult(DeviceMaintenanceStatus.NOT_FOUND, reasons=(
        f"There is no device with id {device_id}. It may have been deleted; reload the list.",))

def _already_exists(known: Device, *, just_added: bool) -> DeviceMaintenanceResult:
    how = "was just added by someone else" if just_added else "is already stored"
    return DeviceMaintenanceResult(DeviceMaintenanceStatus.ALREADY_EXISTS, existing_device_id=known.pk, reasons=(
        f"A device with MAC {known.mac_address} {how}. Edit that one instead.",))
