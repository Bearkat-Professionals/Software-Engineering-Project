"""2.2.5.2 Record Observations and Evidence (#41), with identification (2.2.4).

After Host Discovery, save what one scan saw. For each host, in its own short unit of
work (OOD 5.1): find the device by MAC, load its stored evidence, identify it (2.2.4),
save it through SH-4, then save one observation and the new evidence (3.1.4). One host
failing never stops the others (SEC-14).

    saver = DeviceRecordSaver(DeviceStore(engine), ReferenceStore(engine, Vendor),
                              ReferenceStore(engine, DeviceType))
    id_rules = IdentificationRules.from_reference(find_reference_rules(engine))    # once per scan
    summary = record_scan_results(scan.pk, user_id, hosts, id_rules, seen_at, engine=engine,
                                  saver=saver, devices=DeviceStore(engine), scans=ScanStore(engine))
    summary.new_flagged_ids     # for 2.2.5.3 Detect Alert Events ("new unidentified device", spec 7.5)
    record_scan_results(..., on_host_done=lambda done, total: print(f"{done} of {total}"))

A scan fills in a device's name and model only while they are empty, so a name a user
typed is never replaced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Protocol

from backend.records import Device, IdentificationEvidence, ScanObservation
from backend.records.enums import NetworkStatus
from backend.services.scan.detect_randomized_mac import is_randomized_mac
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.evidence import UNKNOWN_VENDOR_ID, EvidenceItem, RawEvidence
from backend.services.scan.identification_manager import IdentificationResult, IdentificationRules, identify
from backend.shared_modules.save_device_record import DeviceChanges, DeviceRecordSaver, SaveOutcome, SaveStatus
from backend.shared_modules.validate_input import validate_mac

# No evidence collectors yet (2.2.3, M6): real scans identify from the OUI and stored evidence.
NO_COLLECTED_EVIDENCE: tuple[RawEvidence, ...] = ()


class UnitOfWorkSource(Protocol):
    """DatabaseEngine is one; tests pass a fake."""
    def unit_of_work(self) -> Any: ...


class DeviceFinder(Protocol):
    """3.1.2's lookup by MAC. DeviceStore is one."""
    def find_by_mac(self, mac_address: str, is_randomized_mac: bool = False,
                    unit: Any = None) -> Device | None: ...


class ObservationEvidenceStore(Protocol):
    """The parts of 3.1.4 / 3.1.5 used here. ScanStore is one."""
    def add_observation(self, observation: ScanObservation, unit: Any = None) -> ScanObservation: ...
    def add_evidence(self, evidence: IdentificationEvidence,
                     unit: Any = None) -> tuple[IdentificationEvidence, bool]: ...
    def evidence_for(self, device_id: int, unit: Any = None) -> list[IdentificationEvidence]: ...


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
                        rules: IdentificationRules, seen_at: datetime, *, engine: UnitOfWorkSource,
                        saver: DeviceRecordSaver, devices: DeviceFinder, scans: ObservationEvidenceStore,
                        on_host_done: Callable[[int, int], None] | None = None) -> RecordSummary:
    """Identify and save every host; one host failing never stops the others (SEC-14).

    on_host_done(done, total), if given, is called with (0, total) first, then after
    each host, saved or not, so the Scan Manager can report progress (#48).
    """
    summary = RecordSummary()
    to_save = _one_per_mac(hosts, summary)
    total = len(to_save)
    if on_host_done is not None:
        on_host_done(0, total)

    for done, host in enumerate(to_save, start=1):
        try:
            with engine.unit_of_work() as unit:
                outcome = _record_host(scan_id, user_id, host, rules, seen_at, saver, devices, scans, unit)
        except Exception as exc:            # SEC-14: this host's unit was rolled back; carry on
            summary.failures.append((host.ip_address, f"{type(exc).__name__}: {exc}"))
        else:
            _count(outcome, host, summary)  # only once the unit has committed
        if on_host_done is not None:
            on_host_done(done, total)
    return summary


def _count(outcome: SaveOutcome, host: DiscoveredHost, summary: RecordSummary) -> None:
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


def _record_host(scan_id: int, user_id: int, host: DiscoveredHost, rules: IdentificationRules,
                 seen_at: datetime, saver: DeviceRecordSaver, devices: DeviceFinder,
                 scans: ObservationEvidenceStore, unit: Any) -> SaveOutcome:
    """Identify and save one host's device, observation and evidence, all in the caller's unit."""
    known = _find_device(host, devices, unit)
    stored = [EvidenceItem.from_record(row) for row in scans.evidence_for(known.pk, unit)] if known else []
    result = identify(host, NO_COLLECTED_EVIDENCE, stored, rules,
                      trusted=bool(known and known.trusted_status), is_new_device=known is None)

    outcome = saver.save(_changes(host, seen_at, result, known), user_id, result.identification.level, unit)
    if outcome.status is SaveStatus.REJECTED:
        return outcome                      # nothing was written

    device = outcome.device
    scans.add_observation(ScanObservation(scan_id=scan_id, device_id=device.pk,
                                          ip_address=host.ip_address, hostname=device.hostname,
                                          status=NetworkStatus.ONLINE, observed_at=seen_at), unit)
    for item in result.new_evidence:        # add_evidence skips evidence the device already has
        scans.add_evidence(item.to_record(device.pk), unit)
    return outcome


def _find_device(host: DiscoveredHost, devices: DeviceFinder, unit: Any) -> Device | None:
    """The stored device for this host's MAC, looked up exactly as SH-4 will (DI-01, DI-02).
    None for a new device, or a MAC SH-4 will reject anyway."""
    checked = validate_mac(host.mac_address)
    if not checked:
        return None
    return devices.find_by_mac(checked.value, is_randomized_mac(checked.value), unit)


def _changes(host: DiscoveredHost, seen_at: datetime, result: IdentificationResult,
             known: Device | None) -> DeviceChanges:
    """What SH-4 saves. Name and model only fill empty fields, so a user's name is kept."""
    found = result.identification
    # hostname stays None: no Host Discovery step finds one yet
    return DeviceChanges(
        mac_address=host.mac_address, current_ip=host.ip_address, seen_at=seen_at,
        oui_vendor_id=found.vendor_id if found.vendor_id != UNKNOWN_VENDOR_ID else None,
        device_type_id=found.device_type_id,
        confidence_score=found.breakdown.score,
        device_name=found.device_name if not (known and known.device_name) else None,
        model=found.model if not (known and known.model) else None)
