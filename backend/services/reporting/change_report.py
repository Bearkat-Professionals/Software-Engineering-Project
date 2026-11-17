"""2.4.3 Change Report: "Compare two scans and return new, missing, and changed devices" (OUT-08).

Part of the OOD's QueryReportService. The decisions are in #153:

  - new:      seen in the later scan, not the earlier one. first_seen_in_this_scan
              tells "never seen before" from "back again".
  - missing:  seen in the earlier scan, not the later one, but only when its old IP
              is inside the later scan's range. One the later scan didn't look for
              isn't missing; it's only counted in outside_scope.
  - changed:  seen in both, with a different IP address or hostname.

    result = ChangeReportService(ScanStore(engine), engine.unit_of_work).report()   # the latest two
    result = service.report(from_id=4, to_id=9)
    result.status       # DONE, REJECTED or NOT_FOUND
    result.record       # a ChangeReport when DONE
    result.reasons      # why not, otherwise

build_change_report() is pure: it gets both scans and their rows and touches nothing.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from backend.data_access.network_data import ComparedObservation, ScanPage
from backend.data_access.network_data.scan_store import COMPARABLE_OUTCOMES
from backend.records import Scan
from backend.records.enums import ScanOutcome, ScanStatus
from backend.services.records.reference_maintenance import MaintenanceResult, MaintenanceStatus

class ChangeKind(str, Enum):
    NEW = "new"
    MISSING = "missing"
    CHANGED = "changed"

@dataclass(frozen=True)
class ComparedScan:
    """What the report says about each of the two scans."""
    scan_id: int
    started_at: datetime
    target_scope: str
    outcome: ScanOutcome

    @classmethod
    def of(cls, scan: Scan) -> ComparedScan:
        return cls(scan.pk, scan.started_at, scan.target_scope, scan.outcome)

@dataclass(frozen=True)
class ReportedDevice:
    """One device in the report. old_* come from the earlier scan, new_* from the later
    one, so a NEW device has no old values and a MISSING device has no new ones."""
    device_id: int
    device_name: str | None
    device_type_name: str
    vendor_name: str
    mac_address: str
    is_randomized_mac: bool
    old_ip: str | None = None
    new_ip: str | None = None
    old_hostname: str | None = None
    new_hostname: str | None = None
    first_seen_in_this_scan: bool = False       

@dataclass(frozen=True)
class ChangeReport:
    from_scan: ComparedScan
    to_scan: ComparedScan
    new: tuple[ReportedDevice, ...]            
    missing: tuple[ReportedDevice, ...]
    changed: tuple[ReportedDevice, ...]
    outside_scope: int                          
    randomized_counts: dict[ChangeKind, int] = field(default_factory=dict)   
    partial_warning: bool = False               

class ScanReader(Protocol):
    """ScanStore is one; tests pass a fake."""

    def find(self, scan_id: int, unit: Any = None) -> Scan | None: ...
    def finished(self, page: int = 1, per_page: int = 20, unit: Any = None) -> ScanPage: ...
    def compare_rows(self, from_id: int, to_id: int, unit: Any = None) -> list[ComparedObservation]: ...


#the service
class ChangeReportService:
    def __init__(self, scans: ScanReader, unit_of_work: Callable[[], AbstractContextManager[Any]]):
        self._scans = scans
        self._unit_of_work = unit_of_work

    def report(self, from_id: int | None = None, to_id: int | None = None) -> MaintenanceResult:
        """Compare two finished scans. With no ids, the latest two."""
        if (from_id is None) != (to_id is None):
            return _rejected("Choose both scans to compare, or neither to compare the latest two.")
        if from_id is not None and from_id == to_id:
            return _rejected(f"Choose two different scans, not scan {from_id} twice.")

        with self._unit_of_work() as unit:
            if from_id is None:
                latest = self._scans.finished(page=1, per_page=2, unit=unit)
                if latest.total < 2:
                    return _rejected(f"Comparing needs two finished scans, and there "
                                     f"{'is' if latest.total == 1 else 'are'} {latest.total}.")
                to_scan, from_scan = latest.scans[0].scan, latest.scans[1].scan
            else:
                from_scan, to_scan = self._scans.find(from_id, unit), self._scans.find(to_id, unit)
                reasons = tuple(f"Scan {scan_id} doesn't exist or hasn't finished."
                                for scan_id, scan in ((from_id, from_scan), (to_id, to_scan))
                                if not is_comparable(scan))
                if reasons:
                    return MaintenanceResult(MaintenanceStatus.NOT_FOUND, reasons=reasons)
                if from_scan.started_at > to_scan.started_at:
                    return _rejected(f"Scan {from_id} started after scan {to_id}; "
                                     "the earlier scan goes first.")
            rows = self._scans.compare_rows(from_scan.pk, to_scan.pk, unit)
        return MaintenanceResult(MaintenanceStatus.DONE, build_change_report(from_scan, to_scan, rows))

def is_comparable(scan: Scan | None) -> bool:
    """Finished, fully or partly (#153). Running and failed scans can't be compared."""
    return scan is not None and scan.status == ScanStatus.COMPLETED and scan.outcome in COMPARABLE_OUTCOMES  


#the comparison (pure)
def build_change_report(from_scan: Scan, to_scan: Scan, rows: Iterable[ComparedObservation]) -> ChangeReport:
    before: dict[int, ComparedObservation] = {}
    after: dict[int, ComparedObservation] = {}
    for row in rows:
        if row.scan_id == from_scan.pk:
            before[row.device_id] = row
        elif row.scan_id == to_scan.pk:
            after[row.device_id] = row

    new = [_reported(row, new=row, first_seen_in_this_scan=_first_seen_in(row, to_scan))
           for device_id, row in after.items() if device_id not in before]

    to_range = _network(to_scan.target_scope)
    missing, outside_scope = [], 0
    for device_id, row in before.items():
        if device_id in after:
            continue
        if _inside(row.ip_address, to_range):
            missing.append(_reported(row, old=row))
        else:
            outside_scope += 1

    changed = [_reported(after[device_id], old=row, new=after[device_id])
               for device_id, row in before.items()
               if device_id in after and _differs(row, after[device_id])]

    lists = {ChangeKind.NEW: _by_ip(new), ChangeKind.MISSING: _by_ip(missing), ChangeKind.CHANGED: _by_ip(changed)}
    return ChangeReport(
        ComparedScan.of(from_scan), ComparedScan.of(to_scan),
        lists[ChangeKind.NEW], lists[ChangeKind.MISSING], lists[ChangeKind.CHANGED], outside_scope,
        {kind: sum(device.is_randomized_mac for device in devices) for kind, devices in lists.items()},
        ScanOutcome.PARTIAL in (from_scan.outcome, to_scan.outcome))

def _reported(device: ComparedObservation, old: ComparedObservation | None = None,
              new: ComparedObservation | None = None, first_seen_in_this_scan: bool = False) -> ReportedDevice:
    """The device's names from `device` (the latest row), its old and new addresses from each scan."""
    return ReportedDevice(
        device.device_id, device.device_name, device.device_type_name, device.vendor_name,
        device.mac_address, device.is_randomized_mac,
        old_ip=old.ip_address if old else None, new_ip=new.ip_address if new else None,
        old_hostname=old.hostname if old else None, new_hostname=new.hostname if new else None,
        first_seen_in_this_scan=first_seen_in_this_scan)

def _first_seen_in(row: ComparedObservation, to_scan: Scan) -> bool:
    """First seen during this scan, not before it: new to the network, not back again."""
    return row.first_seen is not None and row.first_seen >= to_scan.started_at

def _differs(old: ComparedObservation, new: ComparedObservation) -> bool:
    """A different IP or hostname. A hostname only in different letter case is the same name,
    and a blank one is no name."""
    return old.ip_address != new.ip_address or _name(old.hostname) != _name(new.hostname)

def _name(hostname: str | None) -> str | None:
    if not hostname:
        return None
    return hostname.strip().casefold() or None

def _network(scope: str | None) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    """The scan's range, or None if what was stored can't be read as one."""
    try:
        return ipaddress.ip_network((scope or "").strip(), strict=False)
    except ValueError:
        return None

def _inside(ip: str | None, network: ipaddress.IPv4Network | ipaddress.IPv6Network | None) -> bool:
    """Is the IP in the range? An IP that's missing or unreadable can't be said to be."""
    if ip is None or network is None:
        return False
    try:
        return ipaddress.ip_address(ip) in network
    except ValueError:
        return False

def _by_ip(devices: list[ReportedDevice]) -> tuple[ReportedDevice, ...]:
    """Sorted by IP as numbers (192.168.1.9 before 192.168.1.10). Devices with no
    readable IP come last; device id breaks ties."""
    return tuple(sorted(devices, key=lambda device: (*_ip_key(device.new_ip or device.old_ip), device.device_id)))

def _ip_key(ip: str | None) -> tuple[int, int, int]:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return (1, 0, 0)
    return (0, address.version, int(address))

def _rejected(reason: str) -> MaintenanceResult:
    return MaintenanceResult(MaintenanceStatus.REJECTED, reasons=(reason,))  