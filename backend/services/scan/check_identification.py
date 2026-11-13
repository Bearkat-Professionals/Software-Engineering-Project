"""Checkpoint for the Identification Manager (2.2.4): what every device was identified as.

    .\\dev scan 192.168.1.0/24 --user blaine       # scan first
    .\\dev ident                                   # then list every device (test database)
    .\\dev ident --database nds                    # another database
    py -3.13 -m backend.services.scan.check_identification

Prints each device's IP, MAC, vendor, type, score, level and flag. The level isn't
stored (spec 7.2: it is worked out from the evidence), so it is worked out again here
with identify(), from the device's stored evidence and today's reference rules: what
the next scan would see. The last column checks the stored score, type and flag
against that: "ok" means they trace to the evidence; anything else means the reference
data changed since the device was last scanned, and the next scan will update it.

Read-only: changes nothing in the database.
"""
from __future__ import annotations

import argparse
import os
import ipaddress
from collections import Counter
from dataclasses import replace

from backend.config import DatabaseConfig
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine
from backend.data_access.network_data import DeviceListing, DeviceStore, ScanStore
from backend.data_access.network_data.device_store import MAX_PER_PAGE
from backend.data_access.reference_data import find_reference_rules
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.evidence import EvidenceItem
from backend.services.scan.identification_manager import IdentificationResult, IdentificationRules, identify
from backend.services.scan.record_scan_results import NO_COLLECTED_EVIDENCE


def every_device(devices: DeviceStore) -> list[DeviceListing]:
    """Every device with its vendor and type names, a page at a time, sorted by IP
    as numbers (.9 before .10); devices with no IP last."""
    found: list[DeviceListing] = []
    page = 1
    while True:
        result = devices.list_page(page=page, per_page=MAX_PER_PAGE)        # This will need to be updated to DeviceSearch once that tracker is merged in
        found.extend(result.devices)
        if page * MAX_PER_PAGE >= result.total:
            break
        page += 1
    return sorted(found, key=_ip_order)


def _ip_order(listing: DeviceListing) -> tuple[int, int, int]:
    """No IP sorts last; IPv4 before IPv6; then numerically."""
    text = listing.device.current_ip
    if not text:
        return 1, 0, 0
    address = ipaddress.ip_address(text)
    return 0, address.version, int(address)


def reidentify(listing: DeviceListing, rules: IdentificationRules, scans: ScanStore) -> IdentificationResult:
    """What identify() gives the device now, from its stored evidence, as a rescan would."""
    device = listing.device
    stored = [EvidenceItem.from_record(row) for row in scans.evidence_for(device.pk)]
    host = DiscoveredHost(device.current_ip or "", device.mac_address, device.is_randomized_mac)
    return identify(host, NO_COLLECTED_EVIDENCE, stored, rules,
                    trusted=device.trusted_status, is_new_device=False)


def check(listing: DeviceListing, result: IdentificationResult) -> str:
    """'ok' when the stored score, type and flag match the evidence; otherwise what differs."""
    device, found = listing.device, result.identification
    differences: list[str] = []
    if device.confidence_score != found.breakdown.score:
        differences.append(f"score {found.breakdown.score} now")
    if device.device_type_id != found.device_type_id:
        differences.append(f"type {found.device_type_id} now")
    if device.flagged_status != result.flagged:
        differences.append(f"flag {'yes' if result.flagged else 'no'} now")
    return "; ".join(differences) or "ok"


def shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 3] + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Show what every device was identified as (2.2.4).")
    parser.add_argument("--database", help="database name (default: the test database)")
    args = parser.parse_args()

    config = DatabaseConfig.from_env()
    database = args.database or os.getenv("TEST_DB_NAME", f"{config.database}_test")
    try:
        engine = DatabaseEngine(replace(config, database=database), pool_size=1)
    except DatabaseConnectionError as exc:
        parser.exit(1, f"{exc}\n")

    devices = every_device(DeviceStore(engine))
    if not devices:
        print(f"No devices in '{database}' yet. Run a scan first: .\\dev scan 192.168.1.0/24 --user <name>")
        return
    rules = IdentificationRules.from_reference(find_reference_rules(engine))
    scans = ScanStore(engine)

    print(f"{len(devices)} device(s) in '{database}'\n")
    print(f"  {'IP':<16} {'MAC':<18} {'Vendor':<24} {'Type':<16} {'Score':>5}  {'Level':<13} {'Flagged':<8} Check")
    levels: Counter[str] = Counter()
    differing = 0
    for listing in devices:
        result = reidentify(listing, rules, scans)
        device, level = listing.device, result.identification.level.value
        note = check(listing, result)
        levels[level] += 1
        differing += note != "ok"
        print(f"  {device.current_ip or '-':<16} {device.mac_address:<18} "
              f"{shorten(listing.vendor_name, 24):<24} {shorten(listing.device_type_name, 16):<16} "
              f"{device.confidence_score:>5}  {level:<13} {'yes' if device.flagged_status else 'no':<8} {note}")

    flagged = sum(listing.device.flagged_status for listing in devices)
    print(f"\n{', '.join(f'{count} {level}' for level, count in sorted(levels.items()))}; {flagged} flagged")
    if differing:
        print(f"{differing} device(s) differ from what their evidence gives today: the reference data "
              "changed since their last scan. The next scan updates them.")


if __name__ == "__main__":
    main()
