"""SH-4 Save Device Record: the one path that creates or updates a device.

Scans call it for every discovered host (2.2.5.2); the add, edit and trust
screens call it later (2.3.1.1, 2.3.1.2, 2.3.1.4). It checks the new values
(SH-1), finds the device by MAC (3.1.2), applies only the fields that were
set, works out the flag (SH-4.1), and stores the change with its audit entry
(3.1.1), all inside the caller's unit of work.

    changes = DeviceChanges(mac_address="28:6F:B9:00:00:01", current_ip="192.168.1.20",
                            seen_at=now, oui_vendor_id=7)
    outcome = save_device(devices, changes, acting_user_id=1,
                          level=provisional_level(7), unit=unit)
    outcome.status      # SaveStatus.CREATED, UPDATED or REJECTED
    outcome.reasons     # plain-English reasons when REJECTED (OUT-04)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from backend.records import Device
from backend.records.enums import IdentificationLevel
from backend.shared_modules.evaluate_flag import evaluate_flag
from backend.shared_modules.validate_input import validate_confidence, validate_ip, validate_mac

UNKNOWN_VENDOR_ID = 1           # the seeded "unknown" vendor every new device starts with (DI-03)

# Fields copied onto the device whenever they are set. The sighting fields and
# oui_vendor_id have their own rules in _apply.
_PLAIN_FIELDS = ("confidence_score", "device_name", "device_type_id", "vendor_id", "model", "trusted_status")


class SaveStatus(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    REJECTED = "rejected"
    CONFLICT = "conflict"       # reserved for M5: changed by someone else since it was loaded (CM-01)


@dataclass(frozen=True)
class DeviceChanges:
    """What to save. The MAC (with is_randomized_mac) says which device; every
    other field left as None means "keep what is stored"."""
    mac_address: str
    is_randomized_mac: bool = False
    # A scan's sighting
    current_ip: str | None = None
    seen_at: datetime | None = None
    hostname: str | None = None
    oui_vendor_id: int | None = None        # a scan's OUI match: used only while the vendor is "unknown"
    confidence_score: int | None = None
    # A user's edit
    device_name: str | None = None
    device_type_id: int | None = None
    vendor_id: int | None = None            # a user's correction: always applied (design 10.4)
    model: str | None = None
    trusted_status: bool | None = None


@dataclass(frozen=True)
class SaveOutcome:
    status: SaveStatus
    device: Device | None = None            # as stored; None when rejected
    reasons: tuple[str, ...] = ()           # why it was rejected (OUT-04)

    @property
    def flagged(self) -> bool:
        return self.device is not None and self.device.flagged_status


class DeviceFinderStorer(Protocol):
    """The two parts of 3.1 Network Data that SH-4 uses. DeviceStore is one; tests pass a fake."""

    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None) -> Device | None: ...

    def store(self, device: Device, changed_by: int, unit: Any = None,
              *, loaded_at: datetime | None = None) -> Device: ...


def save_device(devices: DeviceFinderStorer, changes: DeviceChanges, acting_user_id: int,
                level: IdentificationLevel, unit: Any = None,
                loaded_at: datetime | None = None) -> SaveOutcome:
    """Create or update the device with this MAC. Nothing is stored when anything is invalid."""
    reasons, changes = _check(changes)
    if reasons:
        return SaveOutcome(SaveStatus.REJECTED, reasons=tuple(reasons))

    stored = devices.find_by_mac(changes.mac_address, changes.is_randomized_mac, unit)
    is_new = stored is None
    device = stored if stored is not None else Device(mac_address=changes.mac_address,
                                                      is_randomized_mac=changes.is_randomized_mac)
    _apply(changes, device)
    device.flagged_status = evaluate_flag(level, device.trusted_status, is_new)

    # loaded_at is not checked yet: the conflict check (CM-01) and edit holds (SH-2)
    # come in M5. DeviceStore.store already accepts loaded_at, so M5 passes it on here.
    saved = devices.store(device, acting_user_id, unit)
    return SaveOutcome(SaveStatus.CREATED if is_new else SaveStatus.UPDATED, saved)


def provisional_level(vendor_id: int | None) -> IdentificationLevel:
    """TEMPORARY, until 2.2.4.4 Assign Level (M3) works the level out from the evidence.

    A device whose vendor is known from an OUI match is partially identified;
    one without is unidentified. Either way it is flagged (spec 7.3, rule 1)."""
    if vendor_id is not None and vendor_id != UNKNOWN_VENDOR_ID:
        return IdentificationLevel.PARTIAL
    return IdentificationLevel.UNIDENTIFIED


def _check(changes: DeviceChanges) -> tuple[list[str], DeviceChanges]:
    """Every reason the changes are invalid, and the changes with SH-1's cleaned-up values."""
    reasons: list[str] = []

    mac = validate_mac(changes.mac_address)
    if not mac:
        reasons.append(mac.reason)

    ip = validate_ip(changes.current_ip) if changes.current_ip is not None else None
    if ip is not None and not ip:
        reasons.append(ip.reason)
    if changes.seen_at is not None and changes.current_ip is None:
        reasons.append("A sighting needs the IP address the device was seen at.")

    score = validate_confidence(changes.confidence_score) if changes.confidence_score is not None else None
    if score is not None and not score:
        reasons.append(score.reason)

    if reasons:
        return reasons, changes
    return [], replace(changes,
                       mac_address=mac.value,
                       current_ip=ip.value if ip is not None else None,
                       confidence_score=score.value if score is not None else None)


def _apply(changes: DeviceChanges, device: Device) -> None:
    """Copy the fields that were set onto the device."""
    if changes.seen_at is not None:                 # a sighting: new IP, last seen, online (FR-04)
        device.record_sighting(changes.current_ip, changes.seen_at, changes.hostname)
    else:
        if changes.current_ip is not None:
            device.current_ip = changes.current_ip
        if changes.hostname is not None:
            device.hostname = changes.hostname

    if changes.oui_vendor_id is not None and device.vendor_id in (None, UNKNOWN_VENDOR_ID):
        device.vendor_id = changes.oui_vendor_id    # a scan never replaces a known vendor (design 10.4)

    for name in _PLAIN_FIELDS:                      # vendor_id comes after the OUI match, so a user wins
        value = getattr(changes, name)
        if value is not None:
            setattr(device, name, value)
