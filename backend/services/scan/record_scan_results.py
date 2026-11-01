"""2.2.5.2 Record Observations and Evidence (#41).

After Host Discovery and OUI matching, save what one scan saw: each device
through SH-4, one observation per device, and its OUI evidence (via 3.1.4).
All network work is already done, so each host gets its own short unit of
work (OOD 5.1), and one host failing never stops the others (SEC-14).

    saver = DeviceRecordSaver(DeviceStore(engine), ReferenceStore(engine, Vendor),
                              ReferenceStore(engine, DeviceType))
    summary = record_scan_results(scan.pk, user_id, hosts, load_oui_lookup(engine), seen_at,
                                  engine=engine, saver=saver, scans=ScanStore(engine))
    summary.new_flagged_ids     # for 2.2.5.3 Detect Alert Events ("new unidentified device", spec 7.5)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Protocol

from backend.records import Device, IdentificationEvidence, ScanObservation
from backend.records.enums import NetworkStatus
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.match_oui import OuiLookup, match_oui
from backend.shared_modules.save_device_record import (DeviceChanges, DeviceRecordSaver, SaveOutcome,
                                                       SaveStatus, provisional_level)


class UnitOfWorkSource(Protocol):
    """DatabaseEngine is one; tests pass a fake."""
    def unit_of_work(self) -> Any: ...


class ObservationEvidenceStore(Protocol):
    """The two parts of 3.1.4 used here. ScanStore is one."""
    def add_observation(self, observation: ScanObservation, unit: Any = None) -> ScanObservation: ...
    def add_evidence(self, evidence: IdentificationEvidence,
                     unit: Any = None) -> tuple[IdentificationEvidence, bool]: ...


@dataclass
class RecordSummary:
    created: int = 0
    updated: int = 0
    skipped_no_mac: int = 0                 # can't be saved: the MAC is the identity (DI-01)
    duplicate_macs: int = 0                 # a second IP for a MAC already recorded in this scan
    failures: list[tuple[str, str]] = field(default_factory=list)   # (ip, reason)
    new_flagged_ids: list[int] = field(default_factory=list)       # new devices that are flagged
    devices: list[Device] = field(default_factory=list)            # every device saved, as stored


def record_scan_results(scan_id: int, user_id: int, hosts: Iterable[DiscoveredHost],
                        oui_lookup: OuiLookup, seen_at: datetime, *, engine: UnitOfWorkSource,
                        saver: DeviceRecordSaver, scans: ObservationEvidenceStore) -> RecordSummary:
    summary = RecordSummary()
    for host in _one_per_mac(hosts, summary):
        try:
            with engine.unit_of_work() as unit:
                outcome = _record_host(scan_id, user_id, host, oui_lookup, seen_at, saver, scans, unit)
        except Exception as exc:            # SEC-14: this host's unit was rolled back; carry on
            summary.failures.append((host.ip_address, f"{type(exc).__name__}: {exc}"))
            continue
        # Counted only once the unit has committed
        if outcome.status is SaveStatus.REJECTED:
            summary.failures.append((host.ip_address, " ".join(outcome.reasons)))
        elif outcome.status is SaveStatus.CREATED:
            summary.created += 1
            if outcome.flagged:
                summary.new_flagged_ids.append(outcome.device.pk)
        else:
            summary.updated += 1
        if outcome.device is not None:
            summary.devices.append(outcome.device)
    return summary


def _one_per_mac(hosts: Iterable[DiscoveredHost], summary: RecordSummary) -> list[DiscoveredHost]:
    """Drop hosts without a MAC, and keep the first host for each MAC (UNIQUE(scan_id, device_id))."""
    kept: dict[str, DiscoveredHost] = {}
    for host in hosts:
        if not host.mac_address:
            summary.skipped_no_mac += 1
        elif (key := host.mac_address.upper().replace("-", ":")) in kept:
            summary.duplicate_macs += 1
        else:
            kept[key] = host
    return list(kept.values())


def _record_host(scan_id: int, user_id: int, host: DiscoveredHost, oui_lookup: OuiLookup,
                 seen_at: datetime, saver: DeviceRecordSaver, scans: ObservationEvidenceStore,
                 unit: Any) -> SaveOutcome:
    """Save one host's device, observation and evidence, all in the caller's unit."""
    match = match_oui(host, oui_lookup)
    vendor_id = match.vendor_id if match else None
    # hostname stays None: no Host Discovery step finds one yet
    changes = DeviceChanges(mac_address=host.mac_address, current_ip=host.ip_address,
                            seen_at=seen_at, oui_vendor_id=vendor_id)
    outcome = saver.save(changes, user_id, provisional_level(vendor_id), unit)
    if outcome.status is SaveStatus.REJECTED:
        return outcome                      # nothing was written

    device = outcome.device
    scans.add_observation(ScanObservation(scan_id=scan_id, device_id=device.pk,
                                          ip_address=host.ip_address, hostname=device.hostname,
                                          status=NetworkStatus.ONLINE, observed_at=seen_at), unit)
    if match:                               # add_evidence skips evidence the device already has
        scans.add_evidence(match.evidence.to_record(device.pk), unit)
    return outcome
