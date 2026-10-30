import unittest

from backend.black_boxes.read_arp_cache import ArpEntry, ArpEntryType
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.read_known_hosts import read_known_hosts
from tests.fakes.fake_arp_cache import FakeArpCache

SCOPE = "192.168.1.0/24"


def arp(ip, mac="70:13:01:65:8E:F7", entry_type=ArpEntryType.DYNAMIC):
    return ArpEntry(ip_address=ip, mac_address=mac, interface_ip="192.168.1.2", entry_type=entry_type)



class TestDiscoveredHost(unittest.TestCase):
    def test_defaults(self):
        host = DiscoveredHost("192.168.1.20")
        self.assertEqual(host.mac_address, None)
        self.assertFalse(host.is_randomized_mac)

    def test_with_mac(self):
        host = DiscoveredHost("192.168.1.20", "70:13:01:65:8E:F7")
        self.assertEqual((host.ip_address, host.mac_address, host.is_randomized_mac),
                         ("192.168.1.20", "70:13:01:65:8E:F7", False))

    def test_is_frozen(self):
        host = DiscoveredHost("192.168.1.20")
        with self.assertRaises(Exception):
            host.ip_address = "192.168.1.21"



class TestReadKnownHosts(unittest.TestCase):
    def test_arp_host_in_scope(self):
        cache = FakeArpCache([arp("192.168.1.1")])
        result = read_known_hosts(SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(result.hosts, [DiscoveredHost("192.168.1.1", "70:13:01:65:8E:F7")])
        self.assertEqual(result.arp_status, "ok")
        self.assertEqual(result.rejected, [])

    def test_reads_the_whole_table_once(self):
        cache = FakeArpCache([arp("192.168.1.1")])
        read_known_hosts(SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(cache.calls, [None])

    def test_arp_host_out_of_scope_is_dropped(self):
        cache = FakeArpCache([arp("192.168.1.1"), arp("192.168.2.1"), arp("10.0.0.1")])
        result = read_known_hosts(SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual([h.ip_address for h in result.hosts], ["192.168.1.1"])

    def test_randomized_mac_is_left_for_2_2_2_5(self):
        cache = FakeArpCache([arp("192.168.1.30", mac="DA:00:00:00:00:01")])
        result = read_known_hosts(SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual(result.hosts, [DiscoveredHost("192.168.1.30", "DA:00:00:00:00:01", False)])

    def test_good_user_address_is_kept_without_mac(self):
        cache = FakeArpCache()
        result = read_known_hosts(SCOPE, [" 192.168.1.40 "], read_arp=cache.read_arp_cache)
        self.assertEqual(result.hosts, [DiscoveredHost("192.168.1.40")])
        self.assertEqual(result.rejected, [])

    def test_bad_user_address_is_rejected_with_reason(self):
        cache = FakeArpCache()
        result = read_known_hosts(SCOPE, ["not an ip", "192.168.1.300", ""], read_arp=cache.read_arp_cache)
        self.assertEqual(result.hosts, [])
        self.assertEqual(result.rejected, [
            ("not an ip", "Invalid IP address: not an ip"),
            ("192.168.1.300", "Invalid IP address: 192.168.1.300"),
            ("", "The IP address field is empty."),
        ])

    def test_user_address_out_of_scope_is_rejected(self):  # SEC-11
        cache = FakeArpCache()
        result = read_known_hosts(SCOPE, ["8.8.8.8", "192.168.2.5"], read_arp=cache.read_arp_cache)
        self.assertEqual(result.hosts, [])
        self.assertEqual([address for address, _ in result.rejected], ["8.8.8.8", "192.168.2.5"])
        self.assertIn("outside the scan range 192.168.1.0/24", result.rejected[0][1])

    def test_mixed_user_addresses(self):
        cache = FakeArpCache()
        result = read_known_hosts(SCOPE, ["192.168.1.5", "junk", "192.168.1.6"], read_arp=cache.read_arp_cache)
        self.assertEqual([h.ip_address for h in result.hosts], ["192.168.1.5", "192.168.1.6"])
        self.assertEqual(len(result.rejected), 1)

    def test_arp_unavailable_still_returns_user_addresses(self):
        cache = FakeArpCache(unavailable=True)
        result = read_known_hosts(SCOPE, ["192.168.1.40"], read_arp=cache.read_arp_cache)
        self.assertEqual(result.arp_status, "unavailable")
        self.assertEqual(result.hosts, [DiscoveredHost("192.168.1.40")])

    def test_arp_crash_is_unavailable_not_an_error(self):  
        def broken_arp():
            raise OSError("fake crash")

        result = read_known_hosts(SCOPE, ["192.168.1.40"], read_arp=broken_arp)
        self.assertEqual(result.arp_status, "unavailable")
        self.assertEqual(result.hosts, [DiscoveredHost("192.168.1.40")])

    def test_arp_empty_is_ok_not_unavailable(self):
        cache = FakeArpCache([])
        result = read_known_hosts(SCOPE, read_arp=cache.read_arp_cache)
        self.assertEqual((result.hosts, result.arp_status), ([], "ok"))

    def test_arp_hosts_come_before_user_hosts(self):
        cache = FakeArpCache([arp("192.168.1.1")])
        result = read_known_hosts(SCOPE, ["192.168.1.40"], read_arp=cache.read_arp_cache)
        self.assertEqual([h.ip_address for h in result.hosts], ["192.168.1.1", "192.168.1.40"])

    def test_no_user_addresses(self):
        for user_addresses in [None, [], ()]:
            with self.subTest(user_addresses=user_addresses):
                cache = FakeArpCache()
                result = read_known_hosts(SCOPE, user_addresses, read_arp=cache.read_arp_cache)
                self.assertEqual((result.hosts, result.rejected), ([], []))

    def test_single_string_is_one_address(self):
        cache = FakeArpCache()
        result = read_known_hosts(SCOPE, "192.168.1.40", read_arp=cache.read_arp_cache)
        self.assertEqual(result.hosts, [DiscoveredHost("192.168.1.40")])

    def test_bad_scope_raises_and_reads_nothing(self):
        for scope in ["8.8.8.0/24", "10.0.0.0/8", "192.168.1.0", "", None]:
            with self.subTest(scope=scope):
                cache = FakeArpCache([arp("192.168.1.1")])
                with self.assertRaises(ValueError):
                    read_known_hosts(scope, read_arp=cache.read_arp_cache)
                self.assertEqual(cache.calls, [])


if __name__ == "__main__":
    unittest.main()