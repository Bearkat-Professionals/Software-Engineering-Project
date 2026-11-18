"""Checkpoint for 2.2.3 Evidence Collection (#151): every evidence collector on your real network.

    .\\dev ev                                          your own subnet (the /24 this computer is on)
    .\\dev ev 192.168.1.0/24
    .\\dev ev 192.168.1.0/24 --methods mdns,ssdp --wait 5
    .\\dev ev 192.168.1.0/24 --ip 192.168.1.20         only these addresses (can repeat)

Asks every address in the range, without running Host Discovery first, so a device that
doesn't answer ping is still asked. Prints, per host, what each method found, the hostname
a scan would save, and any method that couldn't run. Saves nothing.

Uses SCAN_EVIDENCE_METHODS and SCAN_EVIDENCE_WAIT_S from .env; --methods and --wait
override them for this run. On Windows the first run may show a Firewall prompt for
Python: allow it on private networks, or mDNS and SSDP hear nothing.

This is not a unit test: what it prints depends on the network you are on.
"""
from __future__ import annotations

import argparse
import ipaddress
import time
from dataclasses import replace

from backend.services.scan.collect_evidence import CollectedEvidence, collect_evidence
from backend.services.scan.collect_ssdp_evidence import local_address_for
from backend.services.scan.scan_manager import EVIDENCE_COLLECTORS, evidence_collectors
from backend.services.scan.scan_settings import ScanSettings
from backend.services.scan.sweep_subnet import list_host_addresses

ANY_PUBLIC_ADDRESS = "8.8.8.8"      

def default_scope(local_address: str | None = None) -> str | None:
    """The /24 around this computer's address, e.g. "192.168.1.0/24", or None if it can't be told."""
    address = local_address or local_address_for([ANY_PUBLIC_ADDRESS])
    if not address:
        return None
    return str(ipaddress.ip_network(f"{address}/24", strict=False))

def report_lines(collected: CollectedEvidence) -> list[str]:
    """What check_evidence prints for each host, sorted by IP. Pure, so it can be tested."""
    lines: list[str] = []
    hosts = set(collected.evidence) | set(collected.hostnames)
    for ip in sorted(hosts, key=ipaddress.ip_address):
        lines.append(f"  {ip:<16} hostname: {collected.hostnames.get(ip, '(none)')}")
        for item in collected.evidence.get(ip, ()):
            lines.append(f"      {item.evidence_type.value:<12} {item.raw_value}")
    for method, reason in collected.unavailable:
        lines.append(f"  UNAVAILABLE  {method}: {reason}")
    return lines

def main() -> None:
    parser = argparse.ArgumentParser(description="Run the evidence collectors (2.2.3) and print what each host said.")
    parser.add_argument("scope", nargs="?", help="the range to ask, e.g. 192.168.1.0/24 (default: your subnet)")
    parser.add_argument("--ip", action="append", default=[], metavar="IP",
                        help="ask only this address in the range (can repeat)")
    parser.add_argument("--methods", metavar="LIST",
                        help="e.g. mdns,ssdp (default: SCAN_EVIDENCE_METHODS in .env, or all)")
    parser.add_argument("--wait", type=float, metavar="SECONDS",
                        help="how long mDNS and SSDP listen, 1 to 10 (default: SCAN_EVIDENCE_WAIT_S, or 5)")
    args = parser.parse_args()

    scope = args.scope or default_scope()
    if scope is None:
        parser.error("couldn't tell which network this computer is on; give a range, e.g. 192.168.1.0/24")
    try:
        settings = ScanSettings.from_env()
        overrides = {}
        if args.methods is not None:
            overrides["evidence_methods"] = tuple(part.strip().lower() for part in args.methods.split(",") if part.strip())
        if args.wait is not None:
            overrides["evidence_wait_s"] = args.wait
        settings = replace(settings, **overrides)           
        hosts = list_host_addresses(scope)
    except ValueError as exc:
        parser.error(str(exc))
    if args.ip:
        outside = [ip for ip in args.ip if ip not in hosts]
        if outside:
            parser.error(f"{', '.join(outside)} isn't in {scope}")
        hosts = args.ip

    collectors = evidence_collectors(settings)
    skipped = [method for method in settings.evidence_methods if method not in EVIDENCE_COLLECTORS]
    if not collectors:
        print("No evidence method to run: SCAN_EVIDENCE_METHODS (or --methods) turns them all off"
              + (f", or they aren't built yet ({', '.join(skipped)})." if skipped else "."))
        return
    print(f"Asking {len(hosts)} address(es) in {scope} with {', '.join(collectors)} "
          f"(listening {settings.evidence_wait_s:g} s)")
    if skipped:
        print(f"  not built yet, skipped: {', '.join(skipped)}")

    started = time.perf_counter()
    collected = collect_evidence(hosts, collectors, settings.evidence_wait_s,
                                 on_done=lambda done, total: print(f"  {done} of {total} methods finished"))
    seconds = time.perf_counter() - started

    print()
    for line in report_lines(collected) or ["  nothing found"]:
        print(line)
    print(f"\n{len(collected.evidence)} host(s) with evidence, {len(collected.hostnames)} with a hostname, "
          f"in {seconds:.1f} s. Nothing was saved.")

if __name__ == "__main__":
    main()