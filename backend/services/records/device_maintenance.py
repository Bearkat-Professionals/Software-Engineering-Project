"""2.3.1 Device Record Maintenance: the changes a user makes to a device by hand.

2.3.1.1 add, 2.3.1.2 edit and 2.3.1.4 mark trusted / flagged / automatic go through
SH-4 Save Device Record, the same path scans use, so a device is checked, flagged
and audited the same way however it changes. 2.3.1.3 delete goes through 3.1.3.
Services never call each other (OOD), so this uses SH-4 and the stores, not the
Scan Manager.

Every method runs in ONE unit of work, so a failure leaves nothing half-saved
(CM-04), and returns one MaintenanceResult the routes turn into JSON.

    maintenance = DeviceMaintenance(engine, saver, DeviceStore(engine))

    form = maintenance.load(7)                          # the device, and when it was read
    result = maintenance.edit(7, DeviceEdit(device_name="Den TV"), acting_user_id=1,
                              loaded_at=form.loaded_at)
    result.status       # DONE, REJECTED, NOT_FOUND, CONFLICT or ALREADY_EXISTS
    result.reasons      # sentences a person can read, when it wasn't DONE
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from backend.data_access.network_data import EditConflictError
from backend.data_access.repository import RecordNotFoundError
from backend.records import Device
from backend.services.scan.detect_randomized_mac import is_randomized_mac
from backend.shared_modules.save_device_record import (DeviceChanges, DeviceRecordSaver, SaveOutcome,
                                                       SaveStatus, provisional_level)
from backend.shared_modules.validate_input import validate_mac

# What each mark sets, as (trusted_status, flag_set_by_user): #PARENT's table.
# "automatic" clears both, so SH-4.1 decides the flag from the identification level.
MARKS: dict[str, tuple[bool, bool]] = {
    "trusted": (True, False),
    "flagged": (False, True),
    "automatic": (False, False),
}


class MaintenanceStatus(str, Enum):
    DONE = "done"
    REJECTED = "rejected"              
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"               
    ALREADY_EXISTS = "already_exists"   


@dataclass(frozen=True)
class MaintenanceResult:
    status: MaintenanceStatus
    device: Device | None = None                
    reasons: tuple[str, ...] = ()               
    changed_by: int | None = None               
    changed_at: datetime | None = None          
    existing_device_id: int | None = None       
    removed: dict[str, int] | None = None       

    @property
    def done(self) -> bool:
        return self.status is MaintenanceStatus.DONE


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
    """load()'s answer for the edit form: the device, and the database time it was read."""
    device: Device | None
    loaded_at: datetime | None

    @property
    def found(self) -> bool:
        return self.device is not None


class UnitOfWork(Protocol):
    """DatabaseEngine is one; tests pass a fake."""

    def unit_of_work(self) -> AbstractContextManager[Any]: ...


class DeviceRecords(Protocol):
    """The parts of 3.1 DeviceStore this uses."""

    def find(self, device_id: int, unit: Any = None) -> Device | None: ...
    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None) -> Device | None: ...
    def delete(self, device_id: int, changed_by: int, unit: Any = None,
               *, loaded_at: datetime | None = None) -> dict[str, int]: ...

class DeviceMaintenance:
    def __init__(self, engine: UnitOfWork, saver: DeviceRecordSaver, devices: DeviceRecords) -> None:
        self._engine = engine
        self._saver = saver
        self._devices = devices

    #add

    def add(self, mac: str, edit: DeviceEdit, acting_user_id: int, *,
            ip: str | None = None, trusted: bool = False) -> MaintenanceResult:
        """A device added by hand. It hasn't been seen by a scan yet, so it starts
        offline with no first or last seen."""
        with self._engine.unit_of_work() as unit:
            checked = validate_mac(mac)
            if checked:                                     # a bad MAC is left for SH-4 to reject
                known = self._devices.find_by_mac(checked.value, is_randomized_mac(checked.value), unit)
                if known is not None:
                    return MaintenanceResult(MaintenanceStatus.ALREADY_EXISTS, existing_device_id=known.pk, reasons=(
                        f"A device with MAC {checked.value} is already stored. Edit that one instead.",))

            changes = DeviceChanges(mac_address=mac, current_ip=ip,         # no seen_at: not a sighting
                                    device_name=edit.device_name, device_type_id=edit.device_type_id,
                                    vendor_id=edit.vendor_id, model=edit.model,
                                    trusted_status=True if trusted else None)
            outcome = self._saver.save(changes, acting_user_id, provisional_level(edit.vendor_id), unit)
            return self._result(outcome, acting_user_id, unit)

    #edit
    def edit(self, device_id: int, edit: DeviceEdit, acting_user_id: int,
             loaded_at: datetime | None) -> MaintenanceResult:
        if edit.empty:
            return _rejected(["Nothing to change. Send a name, type, vendor or model."])
        with self._engine.unit_of_work() as unit:
            device, refused = self._find(device_id, unit)
            if refused is not None:
                return refused
            changes = DeviceChanges(mac_address=device.mac_address,
                                    device_name=edit.device_name, device_type_id=edit.device_type_id,
                                    vendor_id=edit.vendor_id, model=edit.model)
            vendor_id = edit.vendor_id if edit.vendor_id is not None else device.vendor_id
            outcome = self._saver.save(changes, acting_user_id, provisional_level(vendor_id), unit, loaded_at)
            return self._result(outcome, acting_user_id, unit)

  
#trusted / flagged / automatic 
    def set_mark(self, device_id: int, mark: str, acting_user_id: int,
                 loaded_at: datetime | None) -> MaintenanceResult:
        if mark not in MARKS:
            return _rejected([f"'{mark}' is not a mark. Use one of: {', '.join(MARKS)}."])
        trusted, flagged = MARKS[mark]
        with self._engine.unit_of_work() as unit:
            device, refused = self._find(device_id, unit)
            if refused is not None:
                return refused
            changes = DeviceChanges(mac_address=device.mac_address,
                                    trusted_status=trusted, flag_set_by_user=flagged)
            outcome = self._saver.save(changes, acting_user_id, provisional_level(device.vendor_id),
                                       unit, loaded_at)
            return self._result(outcome, acting_user_id, unit)

#delete 
    def delete(self, device_id: int, acting_user_id: int, loaded_at: datetime | None) -> MaintenanceResult:
        """Its observations and evidence go with it (DI-05); the counts come back in removed."""
        try:
            with self._engine.unit_of_work() as unit:
                removed = self._devices.delete(device_id, acting_user_id, unit, loaded_at=loaded_at)
                return MaintenanceResult(MaintenanceStatus.DONE, changed_by=acting_user_id,
                                         changed_at=_database_now(unit), removed=dict(removed))
        except RecordNotFoundError:             # the unit has rolled back by the time we get here
            return _not_found(device_id)
        except EditConflictError as conflict:
            return MaintenanceResult(MaintenanceStatus.CONFLICT, changed_by=conflict.changed_by,
                                     changed_at=conflict.changed_at, reasons=(
                                         f"This device was changed at {conflict.changed_at:%H:%M} after you "
                                         "opened it, so it wasn't deleted. Reload it to see the change.",))

#for the edit form

    def load(self, device_id: int) -> LoadedDevice:
        """The device and when it was read, from the database's clock in the same unit of
        work. Send loaded_at back with the edit so a change made in between is caught."""
        with self._engine.unit_of_work() as unit:
            device = self._devices.find(device_id, unit)
            if device is None:
                return LoadedDevice(None, None)
            return LoadedDevice(device, _database_now(unit))  

    
#helpers 

    def _find(self, device_id: int, unit: Any) -> tuple[Device | None, MaintenanceResult | None]:
        """The device by id, or the result to return instead.

        SH-4 saves by MAC. Several devices can share one randomized MAC (DI-02), and
        SH-4 would then change the newest one, which may not be this one. Refuse that
        rather than change the wrong device."""
        device = self._devices.find(device_id, unit)
        if device is None:
            return None, _not_found(device_id)
        by_mac = self._devices.find_by_mac(device.mac_address, device.is_randomized_mac, unit)
        if by_mac is None or by_mac.pk != device.pk:
            return None, _rejected([f"Another device shares this randomized MAC ({device.mac_address}), "
                                    "so this one can't be changed by hand yet."])
        return device, None

    def _result(self, outcome: SaveOutcome, acting_user_id: int, unit: Any) -> MaintenanceResult:
        """SH-4's outcome as this service's result."""
        if outcome.status in (SaveStatus.CREATED, SaveStatus.UPDATED):
            return MaintenanceResult(MaintenanceStatus.DONE, outcome.device, changed_by=acting_user_id,
                                     changed_at=_database_now(unit))
        if outcome.status is SaveStatus.CONFLICT:
            return MaintenanceResult(MaintenanceStatus.CONFLICT, reasons=outcome.reasons,
                                     changed_by=outcome.changed_by, changed_at=outcome.changed_at)
        return MaintenanceResult(MaintenanceStatus.REJECTED, reasons=outcome.reasons)


def _database_now(unit: Any) -> datetime:
    """The database's current time to the microsecond. NOW() alone drops the fraction, and the
    audit log keeps it (DATETIME(6)), so a form loaded in the same second as a save would
    look older than that save and get a false CONFLICT."""
    unit.execute("SELECT NOW(6) AS now")
    return unit.fetchone()["now"]


def _rejected(reasons: list[str]) -> MaintenanceResult:
    return MaintenanceResult(MaintenanceStatus.REJECTED, reasons=tuple(reasons))


def _not_found(device_id: int) -> MaintenanceResult:
    return MaintenanceResult(MaintenanceStatus.NOT_FOUND, reasons=(
        f"There is no device with id {device_id}. It may have been deleted; reload the list.",))