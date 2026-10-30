"""Tests for 2.2.2.5 Detect Randomized MAC. Pure logic, no network.

    py -3.13 -m unittest tests.services.scan.test_detect_randomized_mac -v
"""
import unittest

from backend.services.scan.detect_randomized_mac import is_randomized_mac, mark_randomized_macs
from backend.services.scan.discovered_host import DiscoveredHost


class IsRandomizedMacTests(unittest.TestCase):
    def test_randomized_examples(self):
        for mac in ["02:00:00:00:00:01", "DA:A1:19:00:00:01", "F2:11:22:33:44:55", "3E:00:00:00:00:01"]:
            with self.subTest(mac=mac):
                self.assertTrue(is_randomized_mac(mac))

    def test_vendor_examples(self):
        for mac in ["3C:22:FB:00:00:01", "70:13:01:65:8E:F7", "AC:DE:48:00:11:22", "00:00:00:00:00:00"]:
            with self.subTest(mac=mac):
                self.assertFalse(is_randomized_mac(mac))

    def test_every_second_digit(self):
        # Randomized exactly when the 0x02 bit is set: 2, 6, A, E (normal devices)
        # and 3, 7, B, F (which also carry the multicast bit)
        for digit in "0123456789ABCDEF":
            with self.subTest(digit=digit):
                self.assertEqual(is_randomized_mac(f"0{digit}:00:00:00:00:01"), digit in "2367ABEF")

    def test_multicast_bit_alone_is_not_randomized(self):
        self.assertFalse(is_randomized_mac("01:00:5E:00:00:16"))    # bit 0x01, not 0x02

    def test_other_formats(self):
        for mac in ["da:a1:19:00:00:01", "DA-A1-19-00-00-01", "daa1.1900.0001", " DA:A1:19:00:00:01 "]:
            with self.subTest(mac=mac):
                self.assertTrue(is_randomized_mac(mac))

    def test_bad_mac_raises(self):
        for mac in ["", None, "not a mac", "DA:A1:19", "GG:00:00:00:00:01"]:
            with self.subTest(mac=mac):
                with self.assertRaises(ValueError):
                    is_randomized_mac(mac)


class MarkRandomizedMacsTests(unittest.TestCase):
    def test_marks_each_host(self):
        hosts = [DiscoveredHost("192.168.1.1", "70:13:01:65:8E:F7"),
                 DiscoveredHost("192.168.1.30", "DA:A1:19:00:00:01")]
        self.assertEqual(mark_randomized_macs(hosts), [
            DiscoveredHost("192.168.1.1", "70:13:01:65:8E:F7", False),
            DiscoveredHost("192.168.1.30", "DA:A1:19:00:00:01", True),
        ])

    def test_host_without_mac_stays_false(self):
        self.assertEqual(mark_randomized_macs([DiscoveredHost("192.168.1.40")]),
                         [DiscoveredHost("192.168.1.40", None, False)])

    def test_keeps_order_and_does_not_change_the_input(self):
        hosts = [DiscoveredHost(f"192.168.1.{n}", "DA:A1:19:00:00:01") for n in (9, 3, 200)]
        before = list(hosts)
        marked = mark_randomized_macs(hosts)
        self.assertEqual([h.ip_address for h in marked], ["192.168.1.9", "192.168.1.3", "192.168.1.200"])
        self.assertEqual(hosts, before)
        self.assertFalse(any(h.is_randomized_mac for h in hosts))

    def test_empty(self):
        self.assertEqual(mark_randomized_macs([]), [])


if __name__ == "__main__":
    unittest.main()
