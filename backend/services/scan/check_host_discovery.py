"""Checkpoint for Host Discovery (#23): the five 2.2.2 leaves against your real network.

    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24
    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24 --address 192.168.1.50
    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24 --no-ping
    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24 --workers 64 --timeout 0.5

Runs the pieces in the order the Scan Manager will use:
read known hosts -> sweep -> resolve MACs -> filter -> mark randomized.
--no-ping acts as if ping can't run, to show the other methods still find hosts.
The sweep uses the scan settings from .env (#81); --workers and --timeout override them.

This is not a unit test: what it prints depends on the network you are on.
"""
import argparse
import ipaddress
import time
from dataclasses import replace
from typing import Any

from backend.black_boxes.timed_network_request import ProbeResult, ProbeStatus
from backend.services.scan.detect_randomized_mac import mark_randomized_macs
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.filter_addresses import filter_addresses
from backend.services.scan.read_known_hosts import read_known_hosts
from backend.services.scan.resolve_mac_addresses import resolve_mac_addresses
from backend.services.scan.scan_settings import ScanSettings
from backend.services.scan.sweep_subnet import sweep_subnet


def blocked_ping(target: str) -> ProbeResult:
    """Stands in for a machine where ping can't run."""
    return ProbeResult(target, ProbeStatus.UNAVAILABLE, 0, "ping turned off by --no-ping")


def found_by(ip: str, known_ips: set[str], swept_ips: set[str]) -> str:
    sources = [name for name, ips in (("known", known_ips), ("ping", swept_ips)) if ip in ips]
    return " + ".join(sources) or "arp lookup"


def settings_from(args: argparse.Namespace) -> ScanSettings:
    """.env first, then --workers / --timeout for this run. Raises ValueError for a bad value."""
    settings = ScanSettings.from_env()
    overrides: dict[str, Any] = {}
    if args.workers is not None:
        overrides["probe_workers"] = args.workers
    if args.timeout is not None:
        overrides["probe_timeout_s"] = args.timeout
    return replace(settings, **overrides)           # runs __post_init__, so overrides are checked too


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Host Discovery (2.2.2) and print the hosts found.")
    parser.add_argument("scope", help="the range to scan, e.g. 192.168.1.0/24")
    parser.add_argument("--address", action="append", default=[], metavar="IP",
                        help="an address to include, as if the user typed it (can repeat)")
    parser.add_argument("--no-ping", action="store_true", help="act as if ping is unavailable")
    parser.add_argument("--workers", type=int, metavar="N",
                        help="pings at once for this run (default: SCAN_PROBE_WORKERS in .env, or 32)")
    parser.add_argument("--timeout", type=float, metavar="SECONDS",
                        help="seconds each ping waits (default: SCAN_PROBE_TIMEOUT_S in .env, or 1)")
    args = parser.parse_args()

    try:
        settings = settings_from(args)
    except ValueError as exc:
        parser.error(str(exc))

    start = time.perf_counter()
    try:
        known = read_known_hosts(args.scope, args.address)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"1. Read known hosts:  {len(known.hosts)} host(s), ARP {known.arp_status}")
    for address, reason in known.rejected:
        print(f"     rejected {address!r}: {reason}")

    sweep_start = time.perf_counter()
    swept, sweep_status = sweep_subnet(args.scope, probe=blocked_ping if args.no_ping else None,
                                       settings=settings)
    sweep_seconds = time.perf_counter() - sweep_start
    print(f"2. Sweep subnet:      {len(swept)} answered, ping {sweep_status} "
          f"({settings.probe_workers} at once, {settings.probe_timeout_s:g} s timeout, {sweep_seconds:.1f} s)")

    hosts = known.hosts + [DiscoveredHost(ip) for ip in swept]   # the sweep returns plain IPs for now
    hosts, arp_status = resolve_mac_addresses(hosts, args.scope)
    print(f"3. Resolve MACs:      {len(hosts)} host(s), ARP {arp_status}")

    hosts = filter_addresses(hosts, args.scope)
    print(f"4. Filter addresses:  {len(hosts)} real device(s)")

    hosts = mark_randomized_macs(hosts)
    print(f"5. Mark randomized:   {sum(h.is_randomized_mac for h in hosts)} randomized")
    seconds = time.perf_counter() - start

    known_ips = {host.ip_address for host in known.hosts}
    swept_ips = set(swept)
    print(f"\n  {'IP':<16} {'MAC':<18} {'Randomized':<11} Found by")
    for host in sorted(hosts, key=lambda h: ipaddress.IPv4Address(h.ip_address)):
        print(f"  {host.ip_address:<16} {host.mac_address or '(none)':<18} "
              f"{'yes' if host.is_randomized_mac else 'no':<11} "
              f"{found_by(host.ip_address, known_ips, swept_ips)}")
    print(f"\n{len(hosts)} device(s) in {seconds:.1f} s")


if __name__ == "__main__":
    main()
