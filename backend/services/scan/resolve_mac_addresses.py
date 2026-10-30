"""2.2.2.4 Resolve MAC Addresses: fill in each host's MAC from the ARP table.

Run it right after the sweep. Pinging an address makes Windows send an ARP
lookup for it, so the table now holds the MAC of everything that answered,
plus devices that ignored the ping but still answered the ARP lookup.

    hosts, status = resolve_mac_addresses(hosts, "192.168.1.0/24")

    status is "ok", or "unavailable" when the ARP table can't be read
    (the hosts then come back unchanged, so the scan carries on)
"""
import ipaddress
from dataclasses import replace

from backend.black_boxes.read_arp_cache import read_arp_cache
from backend.services.scan.discovered_host import DiscoveredHost
from backend.shared_modules.validate_input import validate_scope


def resolve_mac_addresses(hosts, scope, read_arp=read_arp_cache):
    result = validate_scope(scope)
    if not result:
        raise ValueError(result.reason)
    network = ipaddress.IPv4Network(result.value)

    try:
        entries = read_arp()                    # the whole table, one command
    except Exception:
        entries = None                          # a crash counts as unavailable (SEC-14)
    if entries is None:
        return list(hosts), "unavailable"

    mac_by_ip = {}
    for entry in entries:
        if ipaddress.IPv4Address(entry.ip_address) in network:
            mac_by_ip.setdefault(entry.ip_address, entry.mac_address)   # first row wins

    resolved = [replace(host, mac_address=mac_by_ip.get(host.ip_address, host.mac_address))
                for host in hosts]
    listed = {host.ip_address for host in hosts}
    arp_only = [DiscoveredHost(ip, mac) for ip, mac in mac_by_ip.items() if ip not in listed]
    return resolved + arp_only, "ok"
