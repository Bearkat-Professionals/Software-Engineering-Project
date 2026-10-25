"""Manual check for BB-1 against this machine's real ARP table (the ticket's "Done when")

    python -m backend.black_boxes.read_arp_cache.check_arp_cache
    python -m backend.black_boxes.read_arp_cache.check_arp_cache 192.168.1.1

    or

    py -m backend.black_boxes.read_arp_cache.check_arp_cache
    py -m backend.black_boxes.read_arp_cache.check_arp_cache 192.168.1.1

This is not a unit test: what it prints depends on the machine and network you are on.
"""
import argparse
from collections import defaultdict

from backend.black_boxes.read_arp_cache import read_arp_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="Print this machine's ARP table through BB-1.")
    parser.add_argument("ip", nargs="?", help="only show this address, e.g. your gateway")
    args = parser.parse_args()

    try:
        entries = read_arp_cache(args.ip)
    except ValueError as exc:
        parser.error(str(exc))

    if entries is None:
        print("ARP table unavailable (arp could not run on this machine).")
        return
    if not entries:
        print("No ARP entries found.")
        return

    by_interface = defaultdict(list)
    for entry in entries:
        by_interface[entry.interface_ip].append(entry)

    for interface_ip, rows in by_interface.items():
        print(f"\nInterface {interface_ip}  ({len(rows)} entries)")
        for entry in rows:
            print(f"  {entry.ip_address:<16} {entry.mac_address}  {entry.entry_type.value}")
    print(f"\n{len(entries)} entries across {len(by_interface)} interface(s)")


if __name__ == "__main__":
    main()
