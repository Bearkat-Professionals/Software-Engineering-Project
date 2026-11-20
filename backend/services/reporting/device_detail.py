"""Get Device Detail (2.4.2, #120).

Build the read-only view used by the device detail screen from the stored
device, reference names, identification evidence and scan history.  Stored
evidence does not say which conflicting items counted, so the service reruns
the pure conflict-resolution and scoring rules.  Retired signatures are still
loaded because they remain part of a device's history (DI-06).

    service = build_device_detail_service(engine)
    detail = service.get_device_detail(device_id)
    history = service.get_device_history(device_id, page=1, per_page=50)

Every call reads through one unit of work, so its result describes one database
moment.  Nothing here changes data or writes an audit entry.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from backend.records import (
    Device,
    DeviceSignature,
    DeviceType,
    IdentificationEvidence,
    Scan,
    ScanObservation,
    Vendor,
)
from backend.records.enums import EvidenceType
from backend.services.scan.calculate_score import ScoreBreakdown, calculate_score
from backend.services.scan.evidence import EvidenceItem
from backend.services.scan.match_signatures import SignatureMatch
from backend.services.scan.resolve_conflicts import Resolution, resolve_conflicts

if TYPE_CHECKING:
    from backend.data_access.database_engine import DatabaseEngine

DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 200
UNKNOWN_REFERENCE_ID = 1


class UnitOfWorkSource(Protocol):
    def unit_of_work(self) -> AbstractContextManager[Any]: ...


class DeviceReader(Protocol):
    def find(self, device_id: int, unit: Any = None) -> Device | None: ...


class ScanReader(Protocol):
    def evidence_for(self, device_id: int, unit: Any = None) -> list[IdentificationEvidence]: ...

    def device_history(
        self,
        device_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
        unit: Any = None,
    ) -> list[ScanObservation]: ...

    def find(self, scan_id: int, unit: Any = None) -> Scan | None: ...


class ReferenceReader(Protocol):
    def find(self, pk: int, unit: Any = None) -> Any | None: ...


class OuiPrefixReader(Protocol):
    def find(self, prefix: str, unit: Any = None) -> Any | None: ...


@dataclass(frozen=True)
class EvidenceDetail:
    """One stored evidence row plus the result text from its signature, if any."""

    evidence: IdentificationEvidence
    resulting_device_name: str | None = None
    resulting_model: str | None = None


@dataclass(frozen=True)
class DeviceDetail:
    """Everything the detail screen needs for one device."""

    device: Device
    vendor_name: str
    device_type_name: str
    kept: tuple[EvidenceDetail, ...]
    dropped: tuple[EvidenceDetail, ...]
    breakdown: ScoreBreakdown
    score_outdated: bool
    inferred: frozenset[str]


@dataclass(frozen=True)
class DeviceHistoryEntry:
    """One observation together with when its scan started."""

    observation: ScanObservation
    scan_id: int
    started_at: datetime | None


@dataclass(frozen=True)
class DeviceHistoryPage:
    """One page of a device's observations and the matching total."""

    entries: tuple[DeviceHistoryEntry, ...]
    page: int
    per_page: int
    total: int


class DeviceDetailService:
    def __init__(
        self,
        engine: UnitOfWorkSource,
        devices: DeviceReader,
        scans: ScanReader,
        vendors: ReferenceReader,
        device_types: ReferenceReader,
        signatures: ReferenceReader,
        oui_prefixes: OuiPrefixReader,
    ) -> None:
        self._engine = engine
        self._devices = devices
        self._scans = scans
        self._vendors = vendors
        self._device_types = device_types
        self._signatures = signatures
        self._oui_prefixes = oui_prefixes

    def get_device_detail(self, device_id: int) -> DeviceDetail | None:
        """Return one device and the evidence behind its identification.

        None means the device does not exist.  Every read joins the same unit of
        work so a concurrent scan or edit cannot produce a mixed-time result.
        """
        with self._engine.unit_of_work() as unit:
            device = self._devices.find(device_id, unit)
            if device is None:
                return None

            vendor = self._find_reference(self._vendors, device.vendor_id, unit)
            device_type = self._find_reference(self._device_types, device.device_type_id, unit)
            records = self._scans.evidence_for(device_id, unit)
            resolution, details = self._resolve(records, unit)
            breakdown = calculate_score(resolution.kept)
            inferred = self._inferred_values(device, records, resolution, unit)

            return DeviceDetail(
                device=device,
                vendor_name=getattr(vendor, "vendor_name", "unknown"),
                device_type_name=getattr(device_type, "category_name", "unknown"),
                kept=tuple(details[item] for item in resolution.kept),
                dropped=tuple(details[item] for item in resolution.dropped),
                breakdown=breakdown,
                score_outdated=breakdown.score != device.confidence_score,
                inferred=frozenset(inferred),
            )

    def get_device_history(
        self,
        device_id: int,
        page: int = 1,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> DeviceHistoryPage | None:
        """Return one page of observations, or None when the device is unknown."""
        _check_page(page, per_page)
        with self._engine.unit_of_work() as unit:
            if self._devices.find(device_id, unit) is None:
                return None
            observations = self._scans.device_history(device_id, unit=unit)
            total = len(observations)
            first = (page - 1) * per_page
            selected = observations[first:first + per_page]

            scans: dict[int, Scan | None] = {}
            entries = []
            for observation in selected:
                scan_id = observation.scan_id
                if scan_id not in scans:
                    scans[scan_id] = self._scans.find(scan_id, unit)
                scan = scans[scan_id]
                entries.append(DeviceHistoryEntry(
                    observation=observation,
                    scan_id=scan_id,
                    started_at=scan.started_at if scan is not None else None,
                ))
        return DeviceHistoryPage(tuple(entries), page, per_page, total)

    def _resolve(
        self,
        records: list[IdentificationEvidence],
        unit: Any,
    ) -> tuple[Resolution, dict[EvidenceItem, EvidenceDetail]]:
        signatures = {}
        for record in records:
            if record.signature_id is not None and record.signature_id not in signatures:
                signatures[record.signature_id] = self._signatures.find(record.signature_id, unit)
        return resolve_evidence(records, signatures)

    def _inferred_values(
        self,
        device: Device,
        records: list[IdentificationEvidence],
        resolution: Resolution,
        unit: Any,
    ) -> set[str]:
        inferred: set[str] = set()
        if resolution.device_type_id is not None and device.device_type_id == resolution.device_type_id:
            inferred.add("device_type")

        winning_names = {match.resulting_device_name for match in resolution.winning_matches
                         if match.resulting_device_name is not None}
        winning_models = {match.resulting_model for match in resolution.winning_matches
                          if match.resulting_model is not None}
        if device.device_name is not None and device.device_name in winning_names:
            inferred.add("device_name")
        if device.model is not None and device.model in winning_models:
            inferred.add("model")

        if device.vendor_id not in (None, UNKNOWN_REFERENCE_ID):
            for record in records:
                if record.evidence_type is not EvidenceType.OUI:
                    continue
                try:
                    prefix = self._oui_prefixes.find(record.raw_value, unit)
                except ValueError:
                    continue                         # old malformed evidence cannot break the report
                if prefix is not None and prefix.vendor_id == device.vendor_id:
                    inferred.add("vendor")
                    break
        return inferred

    @staticmethod
    def _find_reference(store: ReferenceReader, pk: int | None, unit: Any) -> Any | None:
        return store.find(pk, unit) if pk is not None else None


def resolve_evidence(records: list[IdentificationEvidence], signatures_by_id: dict[int, DeviceSignature | None]) -> tuple[Resolution, dict[EvidenceItem, EvidenceDetail]]:   #Javier change
    matches: list[SignatureMatch] = []
    other: list[EvidenceItem] = []
    details: dict[EvidenceItem, EvidenceDetail] = {}

    for record in records:
        item = EvidenceItem.from_record(record)
        signature = None
        if record.signature_id is not None:
            signature = signatures_by_id.get(record.signature_id)

        details[item] = EvidenceDetail(record, getattr(signature, "resulting_device_name", None), getattr(signature, "resulting_model", None))
        if signature is None:
            other.append(item)
        else:
            matches.append(SignatureMatch(item, signature.device_type_id, signature.resulting_device_name, signature.resulting_model))

    return resolve_conflicts(matches, other), details





def build_device_detail_service(engine: DatabaseEngine) -> DeviceDetailService:
    """Build the service with the real data-access stores."""
    # Kept local so the fake-backed unit tests do not require a database driver.
    from backend.data_access.network_data import DeviceStore, ScanStore
    from backend.data_access.reference_data import OuiPrefixStore, ReferenceStore

    return DeviceDetailService(
        engine,
        DeviceStore(engine),
        ScanStore(engine),
        ReferenceStore(engine, Vendor),
        ReferenceStore(engine, DeviceType),
        ReferenceStore(engine, DeviceSignature),
        OuiPrefixStore(engine),
    )


def _check_page(page: int, per_page: int) -> None:
    if page < 1 or not 1 <= per_page <= MAX_PER_PAGE:
        raise ValueError(f"page must be 1 or more and per_page 1 to {MAX_PER_PAGE}")
