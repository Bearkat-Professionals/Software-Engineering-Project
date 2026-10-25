"""Fake BB-1 for unit tests: answers like read_arp_cache(), with no real ARP table

    arp = FakeArpCache([ArpEntry(...), ...])
    arp.read_arp_cache()                # every entry
    arp.read_arp_cache("10.0.0.1")      # just that IP, [] if it isn't there
    assert arp.calls == [None, "10.0.0.1"]

    FakeArpCache(unavailable=True).read_arp_cache()   # None, like a machine where arp can't run
"""

from backend.black_boxes.read_arp_cache import ArpEntry


class FakeArpCache:
    """A fake ARP table holding `entries`. With unavailable=True every read returns None."""

    def __init__(self, entries=(), unavailable: bool = False):
        self.entries = list(entries)
        self.unavailable = unavailable
        self.calls: list[str | None] = []     # the ip argument of every call, in order

    def read_arp_cache(self, ip: str | None = None) -> list[ArpEntry] | None:
        self.calls.append(ip)
        if self.unavailable:
            return None
        if ip is None:
            return list(self.entries)
        return [entry for entry in self.entries if entry.ip_address == ip]
