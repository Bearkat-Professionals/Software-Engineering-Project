"""BB-1 Read ARP Cache (black box).

Reads the host's ARP table without admin rights. Callers import only from
this package:

    from backend.black_boxes.read_arp_cache import read_arp_cache, ArpEntry, ArpEntryType

    read_arp_cache()            -> list of ArpEntry, or None if unavailable
    read_arp_cache("10.0.0.1")  -> just that IP ([] if it isn't in the table)

The parser is deliberately not exported: it is an internal detail, imported
directly only by this box's own unit tests.
"""
from backend.black_boxes.read_arp_cache.arp_entry import ArpEntry, ArpEntryType
from backend.black_boxes.read_arp_cache.read_arp_cache import read_arp_cache

__all__ = ["ArpEntry", "ArpEntryType", "read_arp_cache"]