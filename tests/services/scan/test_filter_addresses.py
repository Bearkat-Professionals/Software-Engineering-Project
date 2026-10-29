import unittest

from backend.black_boxes.read_arp_cache import ArpEntry, ArpEntryType
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.filter_addresses import filter_addresses
from backend.services.scan.read_known_hosts import read_known_hosts
from tests.fakes.fake_arp_cache import FakeArpCache

SCOPE = "192.168.1.0/24"


def ips(hosts):
    return [host.ip_address for host in hosts]



class TestFilterAddresses(unittest.TestCase):
    def test_real_host_is_kept(self):
        hosts = [DiscoveredHost("192.168.1.20")]
        self.assertEqual(filter_addresses(hosts, SCOPE), hosts)

    def test_removes_network_address(self):
        self.assertEqual(filter_addresses([DiscoveredHost("192.168.1.0")], SCOPE), [])

    def test_removes_broadcast_address(self):
        self.assertEqual(filter_addresses([DiscoveredHost("192.168.1.255")], SCOPE), [])

    def test_network_and_broadcast_follow_the_scope(self):
        hosts = [DiscoveredHost(ip) for ip in ["10.0.4.0", "10.0.4.1", "10.0.5.255", "10.0.7.255"]]
        self.assertEqual(ips(filter_addresses(hosts, "10.0.4.0/22")), ["10.0.4.1", "10.0.5.255"])

    def test_slash_31_and_32_keep_every_address(self):
        self.assertEqual(ips(filter_addresses([DiscoveredHost("192.168.1.50")], "192.168.1.50/32")),
                         ["192.168.1.50"])
        pair = [DiscoveredHost("192.168.1.0"), DiscoveredHost("192.168.1.1")]
        self.assertEqual(filter_addresses(pair, "192.168.1.0/31"), pair)

    def test_removes_multicast(self):
        for ip in ["224.0.0.1", "224.0.0.22", "224.0.0.251", "239.255.255.250"]:
            with self.subTest(ip=ip):
                self.assertEqual(filter_addresses([DiscoveredHost(ip)], SCOPE), [])

    def test_removes_loopback(self):
        for ip in ["127.0.0.1", "127.255.255.254"]:
            with self.subTest(ip=ip):
                self.assertEqual(filter_addresses([DiscoveredHost(ip)], SCOPE), [])

    def test_removes_zero_and_limited_broadcast(self):
        for ip in ["0.0.0.0", "255.255.255.255"]:
            with self.subTest(ip=ip):
                self.assertEqual(filter_addresses([DiscoveredHost(ip)], SCOPE), [])

    def test_removes_invalid_addresses(self):
        for ip in ["", "not an ip", "192.168.1.300", None, "fe80::1"]:
            with self.subTest(ip=ip):
                self.assertEqual(filter_addresses([DiscoveredHost(ip)], SCOPE), [])

    def test_duplicate_keeps_the_copy_with_a_mac(self):
        bare = DiscoveredHost("192.168.1.20")
        with_mac = DiscoveredHost("192.168.1.20", "70:13:01:65:8E:F7")
        self.assertEqual(filter_addresses([bare, with_mac], SCOPE), [with_mac])
        self.assertEqual(filter_addresses([with_mac, bare], SCOPE), [with_mac])

    def test_duplicate_without_macs_keeps_one(self):
        hosts = [DiscoveredHost("192.168.1.20"), DiscoveredHost("192.168.1.20")]
        self.assertEqual(filter_addresses(hosts, SCOPE), [DiscoveredHost("192.168.1.20")])

    def test_duplicate_both_with_mac_keeps_the_first(self):
        first = DiscoveredHost("192.168.1.20", "70:13:01:65:8E:F7")
        second = DiscoveredHost("192.168.1.20", "AC:DE:48:00:11:22")
        self.assertEqual(filter_addresses([first, second], SCOPE), [first])

    def test_keeps_first_seen_order(self):
        hosts = [DiscoveredHost(ip) for ip in ["192.168.1.9", "192.168.1.3", "192.168.1.200"]]
        hosts.append(DiscoveredHost("192.168.1.3", "70:13:01:65:8E:F7"))
        self.assertEqual(ips(filter_addresses(hosts, SCOPE)), ["192.168.1.9", "192.168.1.3", "192.168.1.200"])

    def test_does_not_change_the_input(self):
        hosts = [DiscoveredHost("192.168.1.255"), DiscoveredHost("192.168.1.20")]
        before = list(hosts)
        filter_addresses(hosts, SCOPE)
        self.assertEqual(hosts, before)

    def test_empty(self):
        self.assertEqual(filter_addresses([], SCOPE), [])

    def test_bad_scope_raises(self):
        for scope in ["8.8.8.0/24", "not a range", None]:
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    filter_addresses([DiscoveredHost("192.168.1.20")], scope)



class TestSavedArpTable(unittest.TestCase):
    def test_only_real_in_scope_devices_come_out(self):
        def row(ip, mac, kind=ArpEntryType.DYNAMIC):
            return ArpEntry(ip, mac, "192.168.1.2", kind)

        
        saved_table = [
            row("192.168.1.1", "70:13:01:65:8E:F7"),                       # router
            row("192.168.1.20", "AC:DE:48:00:11:22"),                      # laptop
            row("192.168.1.30", "DA:00:00:00:00:01"),                      # phone, randomized MAC
            row("192.168.1.255", "FF:FF:FF:FF:FF:FF", ArpEntryType.STATIC),
            row("224.0.0.22", "01:00:5E:00:00:16", ArpEntryType.STATIC),
            row("224.0.0.251", "01:00:5E:00:00:FB", ArpEntryType.STATIC),
            row("239.255.255.250", "01:00:5E:7F:FF:FA", ArpEntryType.STATIC),
            row("255.255.255.255", "FF:FF:FF:FF:FF:FF", ArpEntryType.STATIC),
            ArpEntry("172.20.0.1", "00:15:5D:00:00:01", "172.20.0.2", ArpEntryType.DYNAMIC),  # other adapter
        ]
        cache = FakeArpCache(saved_table)
        known = read_known_hosts(SCOPE, ["192.168.1.20", "192.168.1.50", "8.8.8.8"],
                                 read_arp=cache.read_arp_cache)
        devices = filter_addresses(known.hosts, SCOPE)

        self.assertEqual(devices, [
            DiscoveredHost("192.168.1.1", "70:13:01:65:8E:F7", False),
            DiscoveredHost("192.168.1.20", "AC:DE:48:00:11:22", False),
            DiscoveredHost("192.168.1.30", "DA:00:00:00:00:01"),       # 2.2.2.5 marks it later
            DiscoveredHost("192.168.1.50"),
        ])
        self.assertEqual([address for address, _ in known.rejected], ["8.8.8.8"])


if __name__ == "__main__":
    unittest.main()