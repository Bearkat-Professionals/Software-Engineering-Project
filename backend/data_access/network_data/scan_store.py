"""3.1.4 Store Scan and Observations and 3.1.5 Retrieve Scan History.

A scan is opened when it starts (status running) and closed once with its
outcome (status completed). While it runs, the scan records one observation
per device it sees, and the identification evidence behind each device.

    scans = ScanStore(engine)
    scan = scans.start(Scan(user_id=user_id, target_scope="192.168.1.0/24"))
    scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=7, ip_address="192.168.1.23"))
    scans.add_evidence(IdentificationEvidence(device_id=7, evidence_type=EvidenceType.OUI,
                                              raw_value="F0:D1:A9", weight=20))
    scans.finish(scan.pk, ScanOutcome.SUCCESS)
    scans.history(datetime(2026, 10, 1), datetime(2026, 11, 1))   # scans with their observations


Scans, observations and evidence are not written to the audit log: they are
the system's own record of what it saw, and the scan already names who
started it (CM-11). Changes people make to devices are audited by DeviceStore.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generator

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import IdentificationEvidence, Scan, ScanObservation
from backend.records.enums import ScanOutcome, ScanStatus

@dataclass(frozen=True)
class ScanWithObservations:
    """One scan from the history and every device it saw."""
    scan: Scan
    observations: list[ScanObservation]

class ScanNotRunningError(Exception):
    """The scan has already finished, so it can't be finished again."""


class ScanStore:
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine
        self._scans = Repository(engine, Scan)
        self._observations = Repository(engine, ScanObservation)
        self._evidence = Repository(engine, IdentificationEvidence)

    def find(self, scan_id: int, unit: Any = None) -> Scan | None:
        return self._scans.get(scan_id, unit)

    def find_running(self, unit: Any = None) -> Scan | None:
        """The scan still running, if any (the newest, should there be several)."""
        running = self._scans.list(unit, status=ScanStatus.RUNNING)
        return running[-1] if running else None

    # ---- 3.1.4 Store Scan ----------------------------------------------------------

    def start(self, scan: Scan, unit: Any = None) -> Scan:
        """Save a new scan as running. started_at defaults to now."""
        if scan.pk is not None:
            raise ValueError("start() takes a new scan; use finish() to close one")
        scan.status = ScanStatus.RUNNING
        scan.outcome = None
        scan.ended_at = None
        if scan.started_at is None:
            scan.started_at = _now()
        with _unit_of_work(self._engine, unit) as cursor:
            self._scans.create(scan, cursor)
            return self._scans.get(scan.pk, cursor)

    def finish(self, scan_id: int, outcome: ScanOutcome, error_message: str | None = None,
               unit: Any = None) -> Scan:
        """Close a running scan with its outcome (success, partial or failed)."""
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute("SELECT `scan_id` FROM `scan` WHERE `scan_id` = %s FOR UPDATE", (scan_id,))
            cursor.fetchall()                                  # lock: only one finish() wins
            scan = self._scans.get(scan_id, cursor)
            if scan is None:
                raise RecordNotFoundError(f"scan {scan_id} does not exist")
            if not scan.is_running:
                raise ScanNotRunningError(f"scan {scan_id} has already finished ({scan.outcome.value})")
            scan.status = ScanStatus.COMPLETED
            scan.outcome = ScanOutcome(outcome)
            scan.error_message = error_message
            scan.ended_at = _now()
            self._scans.update(scan, cursor)
            return self._scans.get(scan_id, cursor)

    # ---- 3.1.4 Store Observations ----------------------------------------------------

    def add_observation(self, observation: ScanObservation, unit: Any = None) -> ScanObservation:
        """Record that this scan saw this device. A scan records each device once:
        seeing it again in the same scan updates that observation."""
        if observation.observed_at is None:
            observation.observed_at = _now()
        with _unit_of_work(self._engine, unit) as cursor:
            same = self._observations.list(cursor, scan_id=observation.scan_id,
                                           device_id=observation.device_id)
            if same:
                observation.observation_id = same[0].pk
                self._observations.update(observation, cursor)
            else:
                self._observations.create(observation, cursor)
        return observation

    def add_evidence(self, evidence: IdentificationEvidence,
                     unit: Any = None) -> tuple[IdentificationEvidence, bool]:
        """Save one piece of evidence for a device, unless the device already has
        the same evidence (same type, value and signature) from an earlier scan.
        Returns (evidence, added): added is False when it was already there."""
        with _unit_of_work(self._engine, unit) as cursor:
            same = self._evidence.list(cursor, device_id=evidence.device_id,
                                       evidence_type=evidence.evidence_type,
                                       raw_value=evidence.raw_value,
                                       signature_id=evidence.signature_id)
            if same:
                existing = same[0]
                if existing.weight != evidence.weight:     # the rule's weight changed since
                    existing.weight = evidence.weight
                    self._evidence.update(existing, cursor)
                return existing, False
            self._evidence.create(evidence, cursor)
        return evidence, True

        # ---- 3.1.5 Retrieve Scan History --------------------------------------------------

    def latest(self, unit: Any = None) -> Scan | None:
        """The most recently started scan, running or finished; None before the first scan."""
        sql = f"SELECT {_columns(Scan, 's')} FROM `scan` s ORDER BY s.`started_at` DESC, s.`scan_id` DESC LIMIT 1"
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(sql)
            row = cursor.fetchone()
        return Scan.from_row(row) if row else None

    def history(self, start: datetime, end: datetime, unit: Any = None) -> list[ScanWithObservations]:
        """Every scan started in [start, end), newest first, each with its observations.
        Two queries in total, however many scans there are."""
        _check_range(start, end)
        scans_sql = (f"SELECT {_columns(Scan, 's')} FROM `scan` s "
                     "WHERE s.`started_at` >= %s AND s.`started_at` < %s "
                     "ORDER BY s.`started_at` DESC, s.`scan_id` DESC")
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(scans_sql, (start, end))
            scans = [Scan.from_row(row) for row in cursor.fetchall()]
            found: dict[int, list[ScanObservation]] = {scan.pk: [] for scan in scans}
            if scans:
                marks = ", ".join(["%s"] * len(scans))
                cursor.execute(f"SELECT {_columns(ScanObservation, 'o')} FROM `scan_observation` o "
                               f"WHERE o.`scan_id` IN ({marks}) ORDER BY o.`observation_id`",
                               tuple(found))
                for row in cursor.fetchall():
                    observation = ScanObservation.from_row(row)
                    found[observation.scan_id].append(observation)
        return [ScanWithObservations(scan, found[scan.pk]) for scan in scans]

    def device_history(self, device_id: int, start: datetime | None = None,
                       end: datetime | None = None, unit: Any = None) -> list[ScanObservation]:
        """Every observation of one device, newest scan first, optionally only
        scans started in [start, end). Shows where and when the device was seen."""
        if start is not None and end is not None:
            _check_range(start, end)
        conditions, params = ["o.`device_id` = %s"], [device_id]
        if start is not None:
            conditions.append("s.`started_at` >= %s")
            params.append(start)
        if end is not None:
            conditions.append("s.`started_at` < %s")
            params.append(end)
        sql = (f"SELECT {_columns(ScanObservation, 'o')} FROM `scan_observation` o "
               "JOIN `scan` s ON s.`scan_id` = o.`scan_id` "
               f"WHERE {' AND '.join(conditions)} "
               "ORDER BY s.`started_at` DESC, o.`observation_id` DESC")
        with _unit_of_work(self._engine, unit) as cursor:
            cursor.execute(sql, tuple(params))
            return [ScanObservation.from_row(row) for row in cursor.fetchall()]

    def evidence_for(self, device_id: int, unit: Any = None) -> list[IdentificationEvidence]:
        """The evidence behind a device's identification, oldest first."""
        return self._evidence.list(unit, device_id=device_id)

def _columns(entity_type: type, alias: str) -> str:
    """alias.`column` for every column of the entity, for a SELECT list."""
    return ", ".join(f"{alias}.`{name}`" for name in entity_type.column_names())

def _check_range(start: datetime, end: datetime) -> None:
    if start >= end:
        raise ValueError("the history range must start before it ends")

def _now() -> datetime:
    """Current time without microseconds: the scan columns are DATETIME, which keeps whole seconds."""
    return datetime.now().replace(microsecond=0)

@contextmanager
def _unit_of_work(engine: DatabaseEngine, unit: Any) -> Generator[Any, None, None]:
    """Join the caller's unit of work if one was passed, otherwise open one."""
    if unit is not None:
        yield unit
    else:
        with engine.unit_of_work() as cursor:
            yield cursor
