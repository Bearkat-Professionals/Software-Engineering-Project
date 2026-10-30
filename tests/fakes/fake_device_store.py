"""Fake 3.1 DeviceStore for unit tests: find_by_mac and store, in memory.

Matches the real store where SH-4 depends on it:
  * store() updates the row with the same MAC instead of adding a second one (FR-04)
  * fixed and randomized MACs are looked up separately (DI-02)
  * a new device gets the database defaults: vendor 1 and type 1, "unknown" (DI-03)
  * every store writes an audit entry (POL-02), kept in .audit
  * callers get copies, so changing a returned Device changes nothing until store()

    devices = FakeDeviceStore([Device(mac_address="AA:BB:CC:00:00:01", trusted_status=True)])
    save_device(devices, changes, acting_user_id=1, level=level, unit="unit-1")
    devices.rows          # device_id -> Device
    devices.audit         # [AuditEntry(changed_by=1, action=AuditAction.UPDATE, device_id=1, unit="unit-1")]
    devices.units_seen    # the unit passed to every call, in order
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from backend.data_access.repository import RecordNotFoundError
from backend.records import Device
from backend.records.enums import AuditAction

UNKNOWN_ID = 1      # what the database fills in for vendor_id and device_type_id (DI-03)


@dataclass(frozen=True)
class AuditEntry:
    changed_by: int
    action: AuditAction
    device_id: int
    unit: Any


class FakeDeviceStore:
    def __init__(self, devices: Iterable[Device] = ()) -> None:
        self.rows: dict[int, Device] = {}
        self.audit: list[AuditEntry] = []
        self.units_seen: list[Any] = []
        self._next_id = 1
        for device in devices:                  # rows from "before the test": no audit entries
            self._insert(deepcopy(device))

    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None) -> Device | None:
        self.units_seen.append(unit)
        found = self._find(mac_address, is_randomized_mac)
        return deepcopy(found) if found is not None else None

    def store(self, device: Device, changed_by: int, unit: Any = None,
              *, loaded_at: datetime | None = None) -> Device:
        self.units_seen.append(unit)
        device = deepcopy(device)               # the caller's object never becomes our row

        if device.pk is None:
            same = self._find(device.mac_address, device.is_randomized_mac)
            if same is not None:
                device.device_id = same.pk      # known MAC: update, never duplicate

        if device.pk is None:
            self._insert(device)
            action = AuditAction.INSERT
        else:
            if device.pk not in self.rows:
                raise RecordNotFoundError(f"device {device.pk} does not exist")
            self.rows[device.pk] = device
            action = AuditAction.UPDATE

        self.audit.append(AuditEntry(changed_by, action, device.pk, unit))
        return deepcopy(self.rows[device.pk])   # "read back", like the real store

    # ---- helpers -----------------------------------------------------------------

    def _find(self, mac_address: str, is_randomized_mac: bool) -> Device | None:
        """Newest row with this MAC and kind, like the real find_by_mac."""
        matches = [device for device in self.rows.values()
                   if device.mac_address.upper() == mac_address.upper()
                   and device.is_randomized_mac == is_randomized_mac]
        return matches[-1] if matches else None

    def _insert(self, device: Device) -> None:
        if device.pk is None:
            device.device_id = self._next_id
        self._next_id = max(self._next_id, device.pk) + 1
        if device.vendor_id is None:
            device.vendor_id = UNKNOWN_ID
        if device.device_type_id is None:
            device.device_type_id = UNKNOWN_ID
        self.rows[device.pk] = device
