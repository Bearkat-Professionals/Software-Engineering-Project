"""Tests for 2.2.2.4 Resolve MAC Addresses, with a fake BB-1 (no network).

    py -3.13 -m unittest tests.services.scan.test_resolve_mac_addresses -v
"""
import unittest

from backend.black_boxes.read_arp_cache import ArpEntry, ArpEntryType
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.resolve_mac_addresses import resolve_mac_addresses
from tests.fakes.fake_arp_cache import FakeArpCache

SCOPE = "192.168.1.0/24"
ROUTER_MAC = "70:13:01:65:8E:F7"
LAPTOP_MAC = "AC:DE:48:00:11:22"


def arp(ip, mac, entry_type=ArpEntryType.DYNAMIC, interface="192.168.1.2"):
    return ArpEntry(ip, mac, interface, entry_type)


class ResolveMacAddressesTests(unittest.TestCase):
    def test_mac_is_filled_in(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC)])
        hosts, status = resolve_mac_addresses([DiscoveredHost("192.168.1.1")], SCOPE,
                                              read_arp=cache.read_arp_cache)
        self.assertEqual(status, "ok")
        self.assertEqual(hosts, [DiscoveredHost("192.168.1.1", ROUTER_MAC)])

    def test_reads_the_whole_table_once(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC)])
        many = [DiscoveredHost(f"192.168.1.{n}") for n in range(1, 255)]
        resolve_mac_addresses(many, SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(cache.calls, [None])                 # one read, not 254

    def test_arp_only_device_is_added_at_the_end(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC), arp("192.168.1.20", LAPTOP_MAC)])
        hosts, _ = resolve_mac_addresses([DiscoveredHost("192.168.1.1")], SCOPE,
                                         read_arp=cache.read_arp_cache)
        self.assertEqual(hosts, [DiscoveredHost("192.168.1.1", ROUTER_MAC),
                                 DiscoveredHost("192.168.1.20", LAPTOP_MAC)])

    def test_out_of_scope_rows_are_not_added(self):
        cache = FakeArpCache([
            arp("172.20.0.1", "00:15:5D:00:00:01", interface="172.20.0.2"),   # another adapter
            arp("224.0.0.22", "01:00:5E:00:00:16", ArpEntryType.STATIC),      # multicast
            arp("192.168.2.1", ROUTER_MAC),                                   # next subnet over
        ])
        hosts, status = resolve_mac_addresses([], SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual((hosts, status), ([], "ok"))

    def test_no_arp_entry_keeps_mac_none(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC)])
        hosts, _ = resolve_mac_addresses([DiscoveredHost("192.168.1.40")], SCOPE,
                                         read_arp=cache.read_arp_cache)
        self.assertEqual(hosts[0], DiscoveredHost("192.168.1.40"))

    def test_existing_mac_is_kept_when_the_table_has_no_entry(self):
        cache = FakeArpCache([])
        known = DiscoveredHost("192.168.1.20", LAPTOP_MAC)
        hosts, _ = resolve_mac_addresses([known], SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(hosts, [known])

    def test_fresh_table_wins_over_an_older_mac(self):
        cache = FakeArpCache([arp("192.168.1.20", "AC:DE:48:99:99:99")])
        hosts, _ = resolve_mac_addresses([DiscoveredHost("192.168.1.20", LAPTOP_MAC)], SCOPE,
                                         read_arp=cache.read_arp_cache)
        self.assertEqual(hosts[0].mac_address, "AC:DE:48:99:99:99")

    def test_duplicate_hosts_both_get_the_mac_and_nothing_is_added(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC)])
        two = [DiscoveredHost("192.168.1.1", ROUTER_MAC), DiscoveredHost("192.168.1.1")]
        hosts, _ = resolve_mac_addresses(two, SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(hosts, [DiscoveredHost("192.168.1.1", ROUTER_MAC)] * 2)  # filter dedupes later

    def test_arp_unavailable_returns_hosts_unchanged(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC)], unavailable=True)
        given = [DiscoveredHost("192.168.1.1"), DiscoveredHost("192.168.1.40")]
        hosts, status = resolve_mac_addresses(given, SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual((hosts, status), (given, "unavailable"))

    def test_arp_crash_is_unavailable_not_an_error(self):
        def broken_arp():
            raise OSError("fake crash")

        given = [DiscoveredHost("192.168.1.1")]
        hosts, status = resolve_mac_addresses(given, SCOPE, read_arp=broken_arp)
        self.assertEqual((hosts, status), (given, "unavailable"))

    def test_empty_table_is_ok_not_unavailable(self):
        given = [DiscoveredHost("192.168.1.1")]
        hosts, status = resolve_mac_addresses(given, SCOPE, read_arp=FakeArpCache([]).read_arp_cache)
        self.assertEqual((hosts, status), (given, "ok"))

    def test_does_not_change_the_input(self):
        cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC), arp("192.168.1.20", LAPTOP_MAC)])
        given = [DiscoveredHost("192.168.1.1")]
        resolve_mac_addresses(given, SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(given, [DiscoveredHost("192.168.1.1")])

    def test_bad_scope_raises_and_reads_nothing(self):
        for scope in ["8.8.8.0/24", "10.0.0.0/8", "192.168.1.0", "", None]:
            with self.subTest(scope=scope):
                cache = FakeArpCache([arp("192.168.1.1", ROUTER_MAC)])
                with self.assertRaises(ValueError):
                    resolve_mac_addresses([], scope, read_arp=cache.read_arp_cache)
                self.assertEqual(cache.calls, [])


if __name__ == "__main__":
    unittest.main()
