"""One row of the operating system's ARP table."""
from dataclasses import dataclass
from enum import Enum


class ArpEntryType(str, Enum):
    DYNAMIC = "dynamic"   # learned from real traffic
    STATIC = "static"     # fixed: broadcast, multicast, or set by hand


@dataclass(frozen=True)
class ArpEntry:
    ip_address: str          # "10.0.0.1"
    mac_address: str         # normalized: "70:13:01:65:8E:F7"
    interface_ip: str        # which adapter's table it came from: "10.0.0.2"
    entry_type: ArpEntryType