"""Checkpoint for Host Discovery (#23): the five 2.2.2 leaves against your real network.

    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24
    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24 --address 192.168.1.50
    py -3.13 -m backend.services.scan.check_host_discovery 192.168.1.0/24 --no-ping

Runs the pieces in the order the Scan Manager will use:
read known hosts -> sweep -> resolve MACs -> filter -> mark randomized.
--no-ping acts as if ping can't run, to show the other methods still find hosts.

This is not a unit test: what it prints depends on the network you are on.
"""
import argparse
import ipaddress
import time

from backend.black_boxes.timed_network_request import ProbeResult, ProbeStatus, ping
from backend.services.scan.detect_randomized_mac import mark_randomized_macs
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.filter_addresses import filter_addresses
from backend.services.scan.read_known_hosts import read_known_hosts
from backend.services.scan.resolve_mac_addresses import resolve_mac_addresses
from backend.services.scan.sweep_subnet import sweep_subnet


def blocked_ping(target: str) -> ProbeResult:
    """Stands in for a machine where ping can't run."""
    return ProbeResult(target, ProbeStatus.UNAVAILABLE, 0, "ping turned off by --no-ping")


def found_by(ip, known_ips, swept_ips):
    sources = [name for name, ips in (("known", known_ips), ("ping", swept_ips)) if ip in ips]
    return " + ".join(sources) or "arp lookup"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Host Discovery (2.2.2) and print the hosts found.")
    parser.add_argument("scope", help="the range to scan, e.g. 192.168.1.0/24")
    parser.add_argument("--address", action="append", default=[], metavar="IP",
                        help="an address to include, as if the user typed it (can repeat)")
    parser.add_argument("--no-ping", action="store_true", help="act as if ping is unavailable")
    args = parser.parse_args()

    start = time.perf_counter()
    try:
        known = read_known_hosts(args.scope, args.address)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"1. Read known hosts:  {len(known.hosts)} host(s), ARP {known.arp_status}")
    for address, reason in known.rejected:
        print(f"     rejected {address!r}: {reason}")

    swept, sweep_status = sweep_subnet(args.scope, probe=blocked_ping if args.no_ping else ping)
    print(f"2. Sweep subnet:      {len(swept)} answered, ping {sweep_status}")

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
