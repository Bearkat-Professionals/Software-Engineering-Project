"""Checkpoint for 2.2.5.2 Record Observations and Evidence (#41): scan your real
network and save what it finds in the TEST database (or --database NAME).

    py -m backend.services.scan.check_save_devices 10.0.0.0/24

Runs Host Discovery in the same order as check_host_discovery.py, loads the OUI
lookup once, opens a scan, records every host, and prints what was saved. Run
it twice: the second run should update the same devices, not add new ones.

Needs the OUI data for vendor names:
    py -m database.scripts.load_oui_data "%USERPROFILE%\\Downloads\\oui.csv"
(The database tests empty vendor and oui_prefix, so reload after running them.)

This is not a unit test: what it prints depends on the network you are on.
"""
import argparse
import ipaddress
import os
from dataclasses import replace
from datetime import datetime

from backend.black_boxes.timed_network_request import ping
from backend.config import DatabaseConfig
from backend.data_access.account_settings import UserStore
from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.network_data import DeviceStore, ScanStore
from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import Repository
from backend.records import DeviceType, Scan, User, Vendor
from backend.records.enums import ScanOutcome
from backend.services.scan.detect_randomized_mac import mark_randomized_macs
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.filter_addresses import filter_addresses
from backend.services.scan.match_oui import load_oui_lookup
from backend.services.scan.read_known_hosts import read_known_hosts
from backend.services.scan.record_scan_results import record_scan_results
from backend.services.scan.resolve_mac_addresses import resolve_mac_addresses
from backend.services.scan.sweep_subnet import sweep_subnet
from backend.shared_modules.save_device_record import UNKNOWN_VENDOR_ID, DeviceRecordSaver


def discover(scope: str) -> list[DiscoveredHost]:
    """Host Discovery, in check_host_discovery.py's order."""
    known = read_known_hosts(scope, [])
    swept, _ = sweep_subnet(scope, probe=ping)
    hosts = known.hosts + [DiscoveredHost(ip) for ip in swept]
    hosts, _ = resolve_mac_addresses(hosts, scope)
    return mark_randomized_macs(filter_addresses(hosts, scope))


def scan_user(engine: DatabaseEngine, database: str) -> int:
    """A scan must name who started it (CM-11). Uses the first user; makes one only in a test database."""
    users = Repository(engine, User).list()
    if users:
        return users[0].pk
    if not database.endswith("_test"):
        raise SystemExit(f"'{database}' has no users. Create one first.")
    return UserStore(engine).create_first_user(User(username="checkpoint", password_hash="not-a-real-hash")).pk


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a range and save the devices (2.2.5.2).")
    parser.add_argument("scope", help="the range to scan, e.g. 10.0.0.0/24")
    parser.add_argument("--database", help="database name (default: the test database)")
    args = parser.parse_args()

    config = DatabaseConfig.from_env()
    database = args.database or os.getenv("TEST_DB_NAME", f"{config.database}_test")
    engine = DatabaseEngine(replace(config, database=database), pool_size=1)

    try:
        hosts = discover(args.scope)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Host Discovery: {len(hosts)} host(s) on {args.scope}")

    lookup = load_oui_lookup(engine)                    # once per scan (design 14.1)
    if not lookup:
        print('No OUI data: run py -m database.scripts.load_oui_data "%USERPROFILE%\\Downloads\\oui.csv"')
    vendor_names = {vendor_id: name for vendor_id, name in lookup.values()}
    vendor_names[UNKNOWN_VENDOR_ID] = "unknown"

    user_id = scan_user(engine, database)
    scans = ScanStore(engine)
    # TEMPORARY: 2.2.1.1 Open Scan Session (the next parent) will open the scan instead
    scan = scans.start(Scan(user_id=user_id, target_scope=args.scope))
    saver = DeviceRecordSaver(DeviceStore(engine), ReferenceStore(engine, Vendor),
                              ReferenceStore(engine, DeviceType))
    summary = record_scan_results(scan.pk, user_id, hosts, lookup, datetime.now().replace(microsecond=0),
                                  engine=engine, saver=saver, scans=scans)
    scans.finish(scan.pk, ScanOutcome.PARTIAL if summary.failures else ScanOutcome.SUCCESS)

    print(f"Scan {scan.pk} in '{database}': {summary.created} created, {summary.updated} updated, "
          f"{summary.skipped_no_mac} skipped (no MAC), {summary.duplicate_macs} duplicate MAC(s), "
          f"{len(summary.failures)} failed")
    for ip, reason in summary.failures:
        print(f"  failed {ip}: {reason}")
    print(f"New flagged devices: {summary.new_flagged_ids or 'none'}")

    print(f"\n  {'ID':<5} {'IP':<16} {'MAC':<18} {'Flagged':<8} Vendor")
    for device in sorted(summary.devices, key=lambda d: ipaddress.IPv4Address(d.current_ip)):
        print(f"  {device.pk:<5} {device.current_ip:<16} {device.mac_address:<18} "
              f"{'yes' if device.flagged_status else 'no':<8} "
              f"{vendor_names.get(device.vendor_id, f'vendor {device.vendor_id}')}")


if __name__ == "__main__":
    main()
