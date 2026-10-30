"""2.2.2.5 Detect Randomized MAC: spot private/randomized MAC addresses (DI-02).

Phones and laptops with "private Wi-Fi address" turned on make up their own
MAC and set the "locally administered" bit: bit 0x02 of the first byte.
Such a MAC isn't tied to one device, so it must never be used to merge
devices.

    is_randomized_mac("DA:A1:19:00:00:01")   -> True   (second digit A has the 0x02 bit)
    is_randomized_mac("3C:22:FB:00:00:01")   -> False  (assigned by the vendor)
    mark_randomized_macs(hosts)              -> the hosts with is_randomized_mac set
"""
from dataclasses import replace

from backend.services.scan.discovered_host import DiscoveredHost
from backend.shared_modules.validate_input import validate_mac

LOCALLY_ADMINISTERED_BIT = 0x02


def is_randomized_mac(mac: str) -> bool:
    """True when the MAC's locally administered bit is set. Raises ValueError for a bad MAC."""
    result = validate_mac(mac)
    if not result:
        raise ValueError(result.reason)
    first_byte = int(result.value[:2], 16)          # "DA:..." -> 0xDA
    return bool(first_byte & LOCALLY_ADMINISTERED_BIT)


def mark_randomized_macs(hosts: list[DiscoveredHost]) -> list[DiscoveredHost]:
    """Return the hosts with is_randomized_mac set. A host with no MAC stays False."""
    return [replace(host, is_randomized_mac=host.mac_address is not None
                    and is_randomized_mac(host.mac_address))
            for host in hosts]
