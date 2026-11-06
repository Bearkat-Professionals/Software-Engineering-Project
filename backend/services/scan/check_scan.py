"""Checkpoint for the Scan Session and Scan Manager (#44, #48): run a whole scan in the
background, the way the app will, on the TEST database (or --database NAME).

    py -3.13 -m backend.services.scan.check_scan 192.168.1.0/24 --user blaine
    .\\dev scan 192.168.1.0/24 --user blaine
    .\\dev scan 192.168.1.0/24 --user blaine --no-ping        # ends partial: ping unavailable
    .\\dev scan 192.168.1.0/24 --user blaine --force-error    # ends failed: saving raises

Run it in a second terminal while one is running: it's refused, naming who started the first.
The user must already exist. Load the OUI list first for vendor names (.\\dev oui ...).
The sweep uses the scan settings from .env (#81).

This is not a unit test: what it prints depends on the network you are on.
"""
from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from dataclasses import replace
from typing import Any, NoReturn

from backend.black_boxes.timed_network_request import ProbeResult, ProbeStatus
from backend.config import DatabaseConfig
from backend.data_access.account_settings import UserStore
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine
from backend.data_access.network_data import ScanStore
from backend.data_access.reference_data import find_reference_rules
from backend.records.enums import ScanOutcome
from backend.services.scan.record_scan_results import RecordSummary
from backend.services.scan.scan_manager import ScanResult, build_scan_manager
from backend.services.scan.scan_session import ScanSessionController, ScanStatusReport
from backend.services.scan.scan_settings import ScanSettings
from backend.shared_modules.save_device_record import UNKNOWN_VENDOR_ID


def blocked_ping(target: str) -> ProbeResult:
    """Stands in for a machine where ping can't run (--no-ping)."""
    return ProbeResult(target, ProbeStatus.UNAVAILABLE, 0, "ping turned off by --no-ping")


def forced_error(*args: Any, **kwargs: Any) -> RecordSummary:
    """Replaces saving (--force-error), to show a crash mid-scan still closes the scan."""
    raise RuntimeError("forced by --force-error")


def print_progress(report: ScanStatusReport, errors_shown: int) -> int:
    """One status line, plus any errors not printed yet. Returns how many errors are printed now."""
    phase = report.phase.value if report.phase is not None else "starting"
    counts = f"{report.done}/{report.total}" if report.total else ""
    print(f"  {report.seconds_elapsed or 0:>4}s  {phase:<18} {counts}")
    for error in report.errors[errors_shown:]:
        print(f"         ! {error}")
    return max(errors_shown, len(report.errors))


def print_result(result: ScanResult, report: ScanStatusReport, vendor_names: dict[int, str]) -> None:
    print(f"\nScan {result.scan_id}: {result.outcome.value.upper()}, started by {report.started_by} "
          f"at {report.started_at:%H:%M:%S}, ended {report.ended_at:%H:%M:%S}")
    for error in result.errors:
        print(f"  ! {error}")

    summary = result.summary
    if summary is None:
        print("Nothing was saved.")
        return
    print(f"{summary.created} created, {summary.updated} updated, {summary.skipped_no_mac} skipped (no MAC), "
          f"{summary.duplicate_macs} duplicate MAC(s), {len(summary.failures)} failed")
    print(f"Alert candidates: {', '.join(a.event.value for a in result.alerts) or 'none'}")

    print(f"\n  {'IP':<16} {'MAC':<18} {'Flagged':<8} Vendor")
    for device in sorted(summary.devices, key=lambda d: ipaddress.IPv4Address(d.current_ip)):
        print(f"  {device.current_ip:<16} {device.mac_address:<18} "
              f"{'yes' if device.flagged_status else 'no':<8} "
              f"{vendor_names.get(device.vendor_id, f'vendor {device.vendor_id}')}")


def main() -> NoReturn:
    parser = argparse.ArgumentParser(description="Run a whole scan in the background and watch it (#44).")
    parser.add_argument("scope", help="the range to scan, e.g. 192.168.1.0/24")
    parser.add_argument("--user", required=True, metavar="USERNAME", help="who starts the scan (must exist)")
    parser.add_argument("--database", help="database name (default: the test database)")
    parser.add_argument("--no-ping", action="store_true", help="act as if ping is unavailable (ends partial)")
    parser.add_argument("--force-error", action="store_true", help="make saving raise (ends failed)")
    parser.add_argument("--recover", action="store_true",
                        help="first close scans left 'running' by a crash; only when no other scan is running")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        settings = ScanSettings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    config = DatabaseConfig.from_env()
    database = args.database or os.getenv("TEST_DB_NAME", f"{config.database}_test")
    try:
        engine = DatabaseEngine(replace(config, database=database), pool_size=3)   # scan thread + status reads
    except DatabaseConnectionError as exc:
        parser.exit(1, f"{exc}\n")

    users = UserStore(engine)
    user = users.find_by_username(args.user)
    if user is None:
        parser.error(f"there's no user named {args.user!r} in '{database}'. Create one first.")

    sessions = ScanSessionController(ScanStore(engine), users)
    if args.recover:
        print(f"Closed {sessions.recover_stuck_scans()} scan(s) left running.")

    finished: list[ScanResult] = []
    manager = build_scan_manager(engine, sessions, settings, probe=blocked_ping if args.no_ping else None,
                                 on_finished=finished.append)
    if args.force_error:
        manager.record_hosts = forced_error

    opened = manager.start(args.scope, user.user_id)
    if not opened.opened:
        print(f"Refused: {opened.reason}")
        sys.exit(1)
    scan_id = opened.scan.scan_id
    print(f"Scan {scan_id} started on {opened.scan.target_scope} by {user.username} in '{database}' "
          f"({settings.probe_workers} at once, {settings.probe_timeout_s:g} s timeout). Ctrl+C stops it.")

    errors_shown = 0
    try:
        while not manager.wait(timeout=1.0):
            errors_shown = print_progress(sessions.status(), errors_shown)
    except KeyboardInterrupt:
        sessions.close(scan_id, ScanOutcome.FAILED, ["stopped with Ctrl+C"])
        print("\nStopped: the scan was closed as failed.")
        sys.exit(130)

    if not finished:
        print(f"\nScan {scan_id} ended, but its close couldn't be saved (see the error above). "
              "The next scan will retry it.")
        sys.exit(1)
    vendor_names = {vendor_id: vendor.vendor_name for vendor_id, vendor in find_reference_rules(engine).vendors.items()}
    vendor_names[UNKNOWN_VENDOR_ID] = "unknown"
    print_result(finished[0], sessions.status(), vendor_names)
    sys.exit(1 if finished[0].outcome is ScanOutcome.FAILED else 0)


if __name__ == "__main__":
    main()
