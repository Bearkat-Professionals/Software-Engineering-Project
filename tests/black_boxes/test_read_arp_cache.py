"""Unit tests for BB-1 Read ARP Cache (#25).

No real network and no real arp command: the parser gets saved `arp -a` text,
and subprocess.run is replaced with a stand-in, so these pass offline.

    python -m unittest tests.black_boxes.test_read_arp_cache -v
"""
import subprocess
import unittest
from unittest import mock

from backend.black_boxes.read_arp_cache import ArpEntry, ArpEntryType, read_arp_cache
from backend.black_boxes.read_arp_cache.parser import normalize_mac, parse_arp_output
from tests.fakes.fake_arp_cache import FakeArpCache

RUN = "backend.black_boxes.read_arp_cache.read_arp_cache.subprocess.run"

ONE_INTERFACE = """
Interface: 10.0.0.2 --- 0x12
  Internet Address      Physical Address      Type
  10.0.0.1              70-13-01-65-8e-f7     dynamic
  10.0.0.50             d4-be-dc-93-b3-1e     dynamic
  224.0.0.22            01-00-5e-00-00-16     static
"""

SEVERAL_INTERFACES = """
Interface: 192.168.75.1 --- 0x4
  Internet Address      Physical Address      Type
  192.168.75.255        ff-ff-ff-ff-ff-ff     static

Interface: 10.0.0.2 --- 0x12
  Internet Address      Physical Address      Type
  10.0.0.1              70-13-01-65-8e-f7     dynamic
"""

GERMAN = """
Schnittstelle: 192.168.1.10 --- 0xb
  Internetadresse       Physische Adresse     Typ
  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamisch
  192.168.1.255         ff-ff-ff-ff-ff-ff     statisch
"""

ROUTER = ArpEntry(ip_address="10.0.0.1", mac_address="70:13:01:65:8E:F7",
                  interface_ip="10.0.0.2", entry_type=ArpEntryType.DYNAMIC)


def arp_finished(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """What subprocess.run hands back when arp finishes."""
    return subprocess.CompletedProcess(["arp", "-a"], returncode, stdout=stdout, stderr="")


# ---- the parser: saved arp -a text in, ArpEntry records out ---------------

class ParserTests(unittest.TestCase):
    def test_one_interface(self):
        entries = parse_arp_output(ONE_INTERFACE)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0], ROUTER)                       # every field, MAC normalized
        self.assertEqual(entries[2].entry_type, ArpEntryType.STATIC)

    def test_several_interfaces_each_entry_keeps_its_own(self):
        entries = parse_arp_output(SEVERAL_INTERFACES)
        self.assertEqual([(e.interface_ip, e.ip_address) for e in entries],
                         [("192.168.75.1", "192.168.75.255"), ("10.0.0.2", "10.0.0.1")])

    def test_non_english_headings_and_types(self):
        entries = parse_arp_output(GERMAN)
        self.assertEqual(entries[0].interface_ip, "192.168.1.10")
        self.assertEqual([e.entry_type for e in entries], [ArpEntryType.DYNAMIC, ArpEntryType.STATIC])

    def test_empty_output(self):
        self.assertEqual(parse_arp_output(""), [])
        self.assertEqual(parse_arp_output("No ARP Entries Found."), [])

    def test_broadcast_and_multicast_are_reported_as_is(self):    # filtering is 2.2.2.3's job
        macs = [e.mac_address for e in parse_arp_output(ONE_INTERFACE + SEVERAL_INTERFACES)]
        self.assertIn("FF:FF:FF:FF:FF:FF", macs)
        self.assertIn("01:00:5E:00:00:16", macs)

    def test_mac_normalization(self):
        self.assertEqual(normalize_mac("aa-bb-cc-dd-ee-ff"), "AA:BB:CC:DD:EE:FF")


# ---- read_arp_cache(): running arp, with subprocess.run faked ---------------

@mock.patch("sys.platform", "win32")          # use the Windows adapter on any machine
class ReadArpCacheTests(unittest.TestCase):
    @mock.patch(RUN, return_value=arp_finished(ONE_INTERFACE))
    def test_success_returns_the_parsed_entries(self, run):
        self.assertEqual(read_arp_cache()[0], ROUTER)
        self.assertEqual(run.call_args.args[0], ["arp", "-a"])

    @mock.patch(RUN, return_value=arp_finished(ONE_INTERFACE))
    def test_an_ip_is_passed_on_to_arp(self, run):
        read_arp_cache("10.0.0.1")
        self.assertEqual(run.call_args.args[0], ["arp", "-a", "10.0.0.1"])

    @mock.patch(RUN, return_value=arp_finished("No ARP Entries Found."))
    def test_ip_not_in_the_table_is_empty_not_unavailable(self, run):
        self.assertEqual(read_arp_cache("203.0.113.5"), [])

    @mock.patch(RUN, side_effect=FileNotFoundError("arp"))
    def test_missing_arp_command_is_unavailable(self, run):
        self.assertIsNone(read_arp_cache())

    @mock.patch(RUN, side_effect=subprocess.TimeoutExpired(cmd="arp", timeout=10))
    def test_timeout_is_unavailable(self, run):
        self.assertIsNone(read_arp_cache())

    @mock.patch(RUN, return_value=arp_finished("", returncode=1))
    def test_failed_run_is_unavailable(self, run):
        self.assertIsNone(read_arp_cache())

    @mock.patch(RUN)
    def test_bad_ip_is_rejected_before_arp_runs(self, run):
        with self.assertRaises(ValueError):
            read_arp_cache("10.0.0.1; whoami")
        run.assert_not_called()

    @mock.patch(RUN)
    def test_other_operating_systems_are_unavailable_for_now(self, run):    # M6
        with mock.patch("sys.platform", "linux"):
            self.assertIsNone(read_arp_cache())
        run.assert_not_called()


# ---- the fake other modules will use -----------------------------------------

class FakeArpCacheTests(unittest.TestCase):
    def test_fake_answers_like_the_real_box(self):
        arp = FakeArpCache([ROUTER])
        self.assertEqual(arp.read_arp_cache(), [ROUTER])
        self.assertEqual(arp.read_arp_cache("10.0.0.1"), [ROUTER])
        self.assertEqual(arp.read_arp_cache("10.0.0.9"), [])
        self.assertEqual(arp.calls, [None, "10.0.0.1", "10.0.0.9"])
        self.assertIsNone(FakeArpCache(unavailable=True).read_arp_cache())


if __name__ == "__main__":
    unittest.main()
