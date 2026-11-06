"""Scan Manager (2.2, #48): runs one whole scan as a background job.

    sessions = ScanSessionController(ScanStore(engine), UserStore(engine))
    manager = build_scan_manager(engine, sessions, ScanSettings.from_env())
    opened = manager.start("192.168.1.0/24", user_id)   # returns at once (CM-09)
    sessions.status()                                   # progress while it runs
    manager.wait()                                      # tests and the checkpoint script

Phases run top to bottom, each passing its output to the next as data (design 2.2):
    1. load the reference data once (3.2.1)
    2. discover hosts (2.2.2): known hosts -> sweep -> resolve MACs -> filter -> mark randomized
    3. save the results (2.2.5.2)
Then the manager decides the outcome (design 8.4) and ALWAYS closes the session, even
after a crash. It sends no alerts: the result lists the events, and on_finished hands
it to the Application Controller after the outcome is saved (design 6.1).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from backend.black_boxes.read_arp_cache import ArpEntry, read_arp_cache
from backend.black_boxes.timed_network_request import Probe, ProbeResult, ping
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.network_data import DeviceStore, ScanStore
from backend.data_access.reference_data import ReferenceRules, ReferenceStore, find_reference_rules
from backend.records import DeviceType, Vendor
from backend.records.enums import AlertEventName, ScanOutcome
from backend.records.scan import Scan
from backend.services.scan.detect_randomized_mac import mark_randomized_macs
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.filter_addresses import filter_addresses
from backend.services.scan.match_oui import OuiLookup, oui_lookup_from_rules
from backend.services.scan.read_known_hosts import read_known_hosts
from backend.services.scan.record_scan_results import RecordSummary, record_scan_results
from backend.services.scan.resolve_mac_addresses import resolve_mac_addresses
from backend.services.scan.scan_session import OpenResult, ScanPhase, ScanSessionController
from backend.services.scan.scan_settings import ScanSettings
from backend.services.scan.sweep_subnet import list_host_addresses, sweep_subnet
from backend.shared_modules.save_device_record import DeviceRecordSaver

logger = logging.getLogger(__name__)

ArpReader = Callable[[], "list[ArpEntry] | None"]
RecordHosts = Callable[..., RecordSummary]      # record_scan_results with engine, saver and scans filled in

OK, UNAVAILABLE, OFF = "ok", "unavailable", "off"


@dataclass(frozen=True)
class AlertCandidate:
    """An event that may need an alert (spec 7.5). The Application Controller decides."""
    event: AlertEventName
    device_id: int | None = None


@dataclass(frozen=True)
class ScanResult:
    scan_id: int
    outcome: ScanOutcome
    summary: RecordSummary | None               # None when the scan stopped before saving
    errors: tuple[str, ...]                     # as saved with the scan
    alerts: tuple[AlertCandidate, ...]
    closed: bool                                # False: the close couldn't be saved; the next open() retries it


@dataclass(frozen=True)
class _Discovery:
    hosts: list[DiscoveredHost]
    arp_status: str                             # OK, UNAVAILABLE or OFF (turned off in the settings)
    ping_status: str

    @property
    def any_method_worked(self) -> bool:
        return OK in (self.arp_status, self.ping_status)


def _arp_turned_off() -> list[ArpEntry]:
    """An empty table: 'arp' isn't in discovery_methods, which isn't the same as unavailable."""
    return []


class ScanManager:
    def __init__(self, sessions: ScanSessionController, *, load_rules: Callable[[], ReferenceRules],
                 record_hosts: RecordHosts, settings: ScanSettings, probe: Probe | None = None,
                 read_arp: ArpReader = read_arp_cache, clock: Callable[[], datetime] = datetime.now,
                 on_finished: Callable[[ScanResult], None] | None = None) -> None:
        self.sessions = sessions
        self.load_rules = load_rules
        self.record_hosts = record_hosts
        self.settings = settings
        self.probe = probe                      # None: ping with settings.probe_timeout_s
        self.read_arp = read_arp
        self.clock = clock
        self.on_finished = on_finished
        self._thread: threading.Thread | None = None

    # ---- starting ----------------------------------------------------------------

    def start(self, scope: str, user_id: int) -> OpenResult:
        """Open a session and run the scan on a background thread; returns at once (CM-09).
        A refusal (bad scope, unknown user, a scan already running) is returned as it is."""
        opened = self.sessions.open(scope, user_id, self.settings)
        if not opened.opened:
            return opened
        scan = opened.scan
        thread = threading.Thread(target=self.run, args=(scan,), name=f"scan-{scan.scan_id}", daemon=True)
        try:
            thread.start()
        except Exception as exc:                # e.g. no threads left: don't leave the scan "running"
            self.sessions.close(scan.scan_id, ScanOutcome.FAILED, [f"scan could not start: {exc}"])
            raise
        self._thread = thread
        return opened

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the background scan; True once it has finished (or none was started)."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # ---- the scan ------------------------------------------------------------------

    def run(self, scan: Scan) -> ScanResult:
        """Run every phase for an opened scan, then close it. Tests and scripts may call it directly."""
        outcome = ScanOutcome.FAILED            # anything that escapes below leaves it failed
        summary: RecordSummary | None = None
        stop_reason: str | None = None
        try:
            rules = self.load_rules()
            found = self._discover(scan.target_scope)
            if not found.any_method_worked:
                stop_reason = "no host discovery method worked: the ARP cache and ping were both unavailable"
            else:
                summary = self._save(scan, found.hosts, oui_lookup_from_rules(rules))
                outcome = self._decide(found, summary)
        except Exception as exc:
            logger.exception("scan %s stopped", scan.scan_id)
            stop_reason = f"scan stopped: {type(exc).__name__}: {exc}"
        finally:
            closed = self._close(scan.scan_id, outcome, stop_reason)

        errors = _lines(closed.error_message) if closed is not None else ((stop_reason,) if stop_reason else ())
        result = ScanResult(scan.scan_id, outcome, summary, errors, _alerts(outcome, summary),
                            closed=closed is not None)
        if result.closed and self.on_finished is not None:
            try:
                self.on_finished(result)        # after the outcome is saved (design 6.1)
            except Exception:
                logger.exception("on_finished failed for scan %s", scan.scan_id)
        return result

    def _discover(self, scope: str) -> _Discovery:
        """2.2.2 in #23's order, following settings.discovery_methods."""
        methods = self.settings.discovery_methods
        read_arp = self.read_arp if "arp" in methods else _arp_turned_off

        known = read_known_hosts(scope, [], read_arp=read_arp)
        if "ping" in methods:
            swept, ping_status = sweep_subnet(scope, probe=self._counting_probe(scope), settings=self.settings)
        else:
            swept, ping_status = [], OFF
        hosts = known.hosts + [DiscoveredHost(ip) for ip in swept]
        hosts, resolve_status = resolve_mac_addresses(hosts, scope, read_arp=read_arp)

        if "arp" not in methods:
            arp_status = OFF
        else:
            arp_status = OK if OK in (known.arp_status, resolve_status) else UNAVAILABLE
        if arp_status == UNAVAILABLE:
            self.sessions.add_error("ARP cache unavailable: MAC addresses could not be read")
        if ping_status == UNAVAILABLE:
            self.sessions.add_error("ping unavailable: no address could be probed")
        return _Discovery(mark_randomized_macs(filter_addresses(hosts, scope)), arp_status, ping_status)

    def _counting_probe(self, scope: str) -> Probe:
        """The probe, wrapped so every finished probe reports "N of total" (2.2.1.3)."""
        total = len(list_host_addresses(scope))
        probe = self.probe or partial(ping, timeout_s=self.settings.probe_timeout_s)
        lock = threading.Lock()
        done = 0
        self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, 0, total)

        def counted(target: str) -> ProbeResult:
            nonlocal done
            try:
                return probe(target)
            finally:                            # counted even if the probe crashed
                with lock:                      # report inside the lock: the count never goes backwards
                    done += 1
                    self.sessions.report_progress(ScanPhase.DISCOVERING_HOSTS, done, total)
        return counted

    def _save(self, scan: Scan, hosts: list[DiscoveredHost], lookup: OuiLookup) -> RecordSummary:
        seen_at = self.clock().replace(microsecond=0)       # DATETIME keeps whole seconds
        summary = self.record_hosts(scan.scan_id, scan.user_id, hosts, lookup, seen_at,
                                    on_host_done=partial(self.sessions.report_progress, ScanPhase.SAVING_RESULTS))
        for ip, reason in summary.failures:
            self.sessions.add_error(f"{ip}: {reason}")
        return summary

    @staticmethod
    def _decide(found: _Discovery, summary: RecordSummary) -> ScanOutcome:
        """partial when a method was unavailable or a host couldn't be saved (design 8.4)."""
        if UNAVAILABLE in (found.arp_status, found.ping_status) or summary.failures:
            return ScanOutcome.PARTIAL
        return ScanOutcome.SUCCESS

    def _close(self, scan_id: int, outcome: ScanOutcome, stop_reason: str | None) -> Scan | None:
        """Always called. Returns the closed scan, or None if the close couldn't be saved."""
        try:
            self.sessions.report_progress(ScanPhase.CLOSING, 0, 1)
            return self.sessions.close(scan_id, outcome, [stop_reason] if stop_reason else ())
        except Exception:
            logger.exception("scan %s: the close couldn't be saved; the next open() retries it", scan_id)
            return None


def _lines(error_message: str | None) -> tuple[str, ...]:
    return tuple(line for line in (error_message or "").split("\n") if line)


def _alerts(outcome: ScanOutcome, summary: RecordSummary | None) -> tuple[AlertCandidate, ...]:
    alerts = [AlertCandidate(AlertEventName.SCAN_FAILED)] if outcome is ScanOutcome.FAILED else []
    if summary is not None:
        alerts += [AlertCandidate(AlertEventName.NEW_UNIDENTIFIED_DEVICE, device_id)
                   for device_id in summary.new_flagged_ids]
    return tuple(alerts)


def build_scan_manager(engine: DatabaseEngine, sessions: ScanSessionController, settings: ScanSettings, *,
                       probe: Probe | None = None, read_arp: ArpReader = read_arp_cache,
                       on_finished: Callable[[ScanResult], None] | None = None) -> ScanManager:
    """A ScanManager on the real database. The one place that knows which stores it uses."""
    saver = DeviceRecordSaver(DeviceStore(engine), ReferenceStore(engine, Vendor),
                              ReferenceStore(engine, DeviceType))
    record = partial(record_scan_results, engine=engine, saver=saver, scans=ScanStore(engine))
    return ScanManager(sessions, load_rules=partial(find_reference_rules, engine), record_hosts=record,
                       settings=settings, probe=probe, read_arp=read_arp, on_finished=on_finished)
