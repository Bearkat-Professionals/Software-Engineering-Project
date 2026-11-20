"""Unit tests for BB-1 Read ARP Cache (#25).

No real network and no real arp command: the parsers get saved output (Windows
`arp -a` below, Linux in tests/fixtures/arp/), and subprocess.run and open are
replaced with stand-ins, so these pass offline on any system.

    python -m unittest tests.black_boxes.test_read_arp_cache -v
"""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from backend.black_boxes.read_arp_cache import ArpEntry, ArpEntryType, read_arp_cache
from backend.black_boxes.read_arp_cache.parser import (normalize_mac, parse_arp_output, parse_linux_addresses,
                                                      parse_linux_neigh, parse_macos_arp, parse_proc_net_arp)
from tests.fakes.fake_arp_cache import FakeArpCache

RUN = "backend.black_boxes.read_arp_cache.read_arp_cache.subprocess.run"
OPEN = "backend.black_boxes.read_arp_cache.read_arp_cache.open"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "arp"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")

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
    def test_other_operating_systems_are_unavailable_for_now(self, run):    # Windows, Linux and macOS are built
        for platform in ("freebsd14", "aix"):
            with self.subTest(platform=platform), mock.patch("sys.platform", platform):
                self.assertIsNone(read_arp_cache())
        run.assert_not_called()


# ---- Linux (#174): the parsers, with recorded output --------------------------------

def linux_entry(ip: str, mac: str, interface_ip: str, entry_type: ArpEntryType) -> ArpEntry:
    return ArpEntry(ip_address=ip, mac_address=mac, interface_ip=interface_ip, entry_type=entry_type)


DYNAMIC, STATIC = ArpEntryType.DYNAMIC, ArpEntryType.STATIC
ADDRESSES = {"lo": "127.0.0.1", "wlp2s0": "192.168.1.50", "docker0": "172.17.0.1", "eth0": "10.0.3.15"}


class LinuxParserTests(unittest.TestCase):
    def test_the_device_addresses(self):
        self.assertEqual(parse_linux_addresses(fixture("linux_ip_addr.txt")), ADDRESSES)   # eth0@if8 -> eth0
        self.assertEqual(parse_linux_addresses(""), {})

    def test_ip_neigh_every_state(self):
        self.assertEqual(parse_linux_neigh(fixture("linux_ip_neigh.txt"), ADDRESSES), [
            linux_entry("192.168.1.1", "02:13:01:65:8E:F7", "192.168.1.50", DYNAMIC),     # REACHABLE
            linux_entry("192.168.1.20", "02:A7:37:12:34:56", "192.168.1.50", DYNAMIC),    # STALE
            linux_entry("192.168.1.30", "02:6F:B9:00:00:01", "192.168.1.50", DYNAMIC),    # router, then DELAY
            linux_entry("192.168.1.40", "02:00:00:00:00:40", "192.168.1.50", DYNAMIC),    # PROBE
            linux_entry("192.168.1.50", "02:00:00:00:00:50", "192.168.1.50", STATIC),     # PERMANENT
            linux_entry("172.17.0.2", "02:42:AC:11:00:02", "172.17.0.1", DYNAMIC),
            linux_entry("10.8.0.1", "02:00:00:00:0A:01", "", STATIC),                     # NOARP; tun0 unknown
        ])                                                                                # FAILED, INCOMPLETE skipped

        def test_words_after_the_state_dont_matter(self):
            for line, entry_type in (("192.168.1.5 dev wlp2s0 lladdr 02:00:00:00:00:05 STALE proto zebra", DYNAMIC),
                                 ("192.168.1.5 dev wlp2s0 lladdr 02:00:00:00:00:05 PERMANENT proto static", STATIC)):
                with self.subTest(line=line):
                    self.assertEqual(parse_linux_neigh(line, ADDRESSES),
                                 [linux_entry("192.168.1.5", "02:00:00:00:00:05", "192.168.1.50", entry_type)])
        self.assertEqual(parse_linux_neigh("192.168.1.5 dev wlp2s0 lladdr 02:00:00:00:00:05 FAILED proto zebra",
                                           ADDRESSES), [])

    def test_a_line_without_lladdr_is_skipped_whatever_its_state(self):
        for line in ("192.168.1.9 dev wlp2s0  FAILED", "192.168.1.9 dev wlp2s0  REACHABLE",
                     "192.168.1.9 dev wlp2s0 lladdr 02:00:00:00:00:09 NONE",
                     "192.168.1.9 dev wlp2s0 lladdr 02:00:00:00:00:09 FAILED",     # newer kernels keep the old MAC
                     "192.168.1.9 dev wlp2s0 lladdr 02:00:00:00:00:09 INCOMPLETE",
                     "192.168.1.9 dev wlp2s0 lladdr",                              # cut off: no crash
                     "192.168.1.9 dev wlp2s0 lladdr notamac STALE", "garbage", ""):
            with self.subTest(line=line):
                self.assertEqual(parse_linux_neigh(line, ADDRESSES), [])

    def test_proc_net_arp_flags_and_the_zero_mac(self):
        self.assertEqual(parse_proc_net_arp(fixture("linux_proc_net_arp.txt"), ADDRESSES), [
            linux_entry("192.168.1.1", "02:13:01:65:8E:F7", "192.168.1.50", DYNAMIC),     # 0x2: complete
            linux_entry("192.168.1.20", "02:A7:37:12:34:56", "192.168.1.50", DYNAMIC),
            linux_entry("192.168.1.50", "02:00:00:00:00:50", "192.168.1.50", STATIC),     # 0x6: has 0x4, permanent
            linux_entry("172.17.0.2", "02:42:AC:11:00:02", "172.17.0.1", DYNAMIC),
        ])                                                                                # header, 0x0 skipped

    def test_proc_zero_mac_alone_is_skipped(self):
        line = "192.168.1.9      0x1         0x2         00:00:00:00:00:00     *        wlp2s0"
        self.assertEqual(parse_proc_net_arp(line, ADDRESSES), [])

    def test_macs_are_upper_case_with_colons(self):
        macs = [e.mac_address for e in parse_linux_neigh(fixture("linux_ip_neigh.txt"), {})]
        self.assertTrue(all(mac == mac.upper() and mac.count(":") == 5 for mac in macs))

    def test_an_unknown_device_has_no_interface_ip(self):
        entries = parse_linux_neigh(fixture("linux_ip_neigh.txt"), {})
        self.assertEqual({e.interface_ip for e in entries}, {""})


# ---- Linux: read_arp_cache() with subprocess.run (and open) faked ---------------------------

def finished(command: list[str], stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def linux_commands(neigh: str | Exception = None, addr: str | Exception = None):
    """A stand-in for subprocess.run answering `ip neigh` and `ip addr` from the fixtures."""
    neigh = fixture("linux_ip_neigh.txt") if neigh is None else neigh
    addr = fixture("linux_ip_addr.txt") if addr is None else addr

    def run(command, **kwargs):
        answer = neigh if command[2] == "neigh" else addr
        if isinstance(answer, BaseException):
            raise answer
        return finished(command, answer)
    return run


@mock.patch("sys.platform", "linux")
class LinuxReadArpCacheTests(unittest.TestCase):
    def test_success_with_the_device_addresses(self):
        with mock.patch(RUN, side_effect=linux_commands()) as run:
            entries = read_arp_cache()
        self.assertEqual(entries[0], linux_entry("192.168.1.1", "02:13:01:65:8E:F7", "192.168.1.50", DYNAMIC))
        self.assertEqual(len(entries), 7)
        commands = [c.args[0] for c in run.call_args_list]
        self.assertEqual(commands, [["ip", "-4", "neigh", "show"], ["ip", "-4", "-o", "addr", "show"]])
        for c in run.call_args_list:
            self.assertEqual(c.kwargs["env"]["LC_ALL"], "C")                         # plain, untranslated output
            self.assertEqual(c.kwargs["timeout"], 10)
            self.assertNotIn("shell", c.kwargs)

    def test_one_ip_is_passed_on_and_kept(self):
        with mock.patch(RUN, side_effect=linux_commands(neigh="192.168.1.20 dev wlp2s0 lladdr 02:a7:37:12:34:56 STALE\n")) as run:
            entries = read_arp_cache("192.168.1.20")
        self.assertEqual(run.call_args_list[0].args[0], ["ip", "-4", "neigh", "show", "to", "192.168.1.20"])
        self.assertEqual([e.ip_address for e in entries], ["192.168.1.20"])

    def test_one_ip_not_in_the_table_is_empty(self):
        with mock.patch(RUN, side_effect=linux_commands(neigh="")):
            self.assertEqual(read_arp_cache("203.0.113.5"), [])

    def test_no_address_map_still_reads_the_table(self):
        with mock.patch(RUN, side_effect=linux_commands(addr=OSError("blocked"))):
            entries = read_arp_cache()
        self.assertEqual(len(entries), 7)
        self.assertEqual({e.interface_ip for e in entries}, {""})

    def test_ip_missing_reads_proc_net_arp(self):
        with mock.patch(RUN, side_effect=FileNotFoundError("ip")), \
             mock.patch(OPEN, mock.mock_open(read_data=fixture("linux_proc_net_arp.txt"))) as opened:
            entries = read_arp_cache()
            one = read_arp_cache("192.168.1.20")
            none = read_arp_cache("192.168.1.99")
        self.assertEqual(opened.call_args.args[0], "/proc/net/arp")
        self.assertEqual([e.ip_address for e in entries], ["192.168.1.1", "192.168.1.20", "192.168.1.50", "172.17.0.2"])
        self.assertEqual([e.ip_address for e in one], ["192.168.1.20"])               # filtered by the ip itself
        self.assertEqual(none, [])

    def test_both_missing_is_unavailable(self):
        with mock.patch(RUN, side_effect=FileNotFoundError("ip")), \
             mock.patch(OPEN, side_effect=FileNotFoundError("/proc/net/arp")):
            self.assertIsNone(read_arp_cache())

    def test_a_timeout_is_unavailable(self):
        with mock.patch(RUN, side_effect=subprocess.TimeoutExpired(cmd="ip", timeout=10)), \
             mock.patch(OPEN) as opened:
            self.assertIsNone(read_arp_cache())
        opened.assert_not_called()                                                  # ip is there; it hung

    def test_a_failed_run_is_unavailable(self):
        with mock.patch(RUN, return_value=finished(["ip"], "", returncode=1)):
            self.assertIsNone(read_arp_cache())

    @mock.patch(RUN)
    def test_bad_ip_is_rejected_before_ip_runs(self, run):
        with self.assertRaises(ValueError):
            read_arp_cache("10.0.0.1; whoami")
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


# ---- macOS (#176): made-up output, never run on a real Mac ----------------------

EN0 = {"en0": "192.168.1.50"}


class MacosParserTests(unittest.TestCase):
    def test_short_macs_are_padded(self):
        self.assertEqual(normalize_mac("70:13:1:65:8e:f7"), "70:13:01:65:8E:F7")
        self.assertEqual(normalize_mac("aa-bb-cc-dd-ee-ff"), "AA:BB:CC:DD:EE:FF")   # Windows still works
        self.assertEqual(normalize_mac("02:13:01:65:8e:f7"), "02:13:01:65:8E:F7")   # Linux still works

    def test_incomplete_is_skipped_and_permanent_is_static(self):
        entries = parse_macos_arp(fixture("macos_arp_an.txt"), EN0)
        self.assertEqual(entries, [
            linux_entry("192.168.1.1", "70:13:01:65:8E:F7", "192.168.1.50", DYNAMIC),
            linux_entry("192.168.1.255", "FF:FF:FF:FF:FF:FF", "192.168.1.50", DYNAMIC),   # broadcast kept
            linux_entry("224.0.0.251", "01:00:5E:00:00:FB", "192.168.1.50", STATIC),      # permanent
        ])
        unknown = parse_macos_arp(fixture("macos_arp_an.txt"), {})
        self.assertEqual({e.interface_ip for e in unknown}, {""})                         # no adapter info

    def test_no_entry_gives_an_empty_list(self):
        self.assertEqual(parse_macos_arp("192.168.1.99 (192.168.1.99) -- no entry", EN0), [])


def fake_mac_commands(arp_out=None, arp_code=0, ipconfig_code=0):
    """Stands in for subprocess.run: answers `arp` and `ipconfig getifaddr`."""
    arp_out = fixture("macos_arp_an.txt") if arp_out is None else arp_out

    def run(command, **kwargs):
        if command[0] == "arp":
            return finished(command, arp_out, arp_code)
        return finished(command, "192.168.1.50\n", ipconfig_code)
    return run


@mock.patch("sys.platform", "darwin")
class MacosReadArpCacheTests(unittest.TestCase):
    def test_success_asks_each_adapter_once(self):
        with mock.patch(RUN, side_effect=fake_mac_commands()) as run:
            entries = read_arp_cache()
        self.assertEqual(len(entries), 3)
        self.assertEqual({e.interface_ip for e in entries}, {"192.168.1.50"})
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["arp", "-an"], ["ipconfig", "getifaddr", "en0"]])
        for c in run.call_args_list:
            self.assertEqual(c.kwargs["env"]["LC_ALL"], "C")

    def test_one_ip_is_passed_to_arp(self):
        line = "192.168.1.1 (192.168.1.1) at 70:13:1:65:8e:f7 on en0 ifscope [ethernet]\n"
        with mock.patch(RUN, side_effect=fake_mac_commands(arp_out=line)) as run:
            entries = read_arp_cache("192.168.1.1")
        self.assertEqual(run.call_args_list[0].args[0], ["arp", "-n", "192.168.1.1"])
        self.assertEqual([e.ip_address for e in entries], ["192.168.1.1"])

    def test_no_entry_is_empty_not_unavailable(self):
        text = "192.168.1.99 (192.168.1.99) -- no entry\n"
        with mock.patch(RUN, side_effect=fake_mac_commands(arp_out=text, arp_code=1)):
            self.assertEqual(read_arp_cache("192.168.1.99"), [])

    def test_missing_command_or_timeout_is_unavailable(self):
        for problem in (FileNotFoundError("arp"), subprocess.TimeoutExpired(cmd="arp", timeout=10)):
            with self.subTest(problem=type(problem).__name__), mock.patch(RUN, side_effect=problem):
                self.assertIsNone(read_arp_cache())

    def test_a_failed_run_is_unavailable(self):
        with mock.patch(RUN, side_effect=fake_mac_commands(arp_out="", arp_code=1)):
            self.assertIsNone(read_arp_cache())

    def test_a_failed_ipconfig_leaves_interface_ip_empty(self):
        with mock.patch(RUN, side_effect=fake_mac_commands(ipconfig_code=1)):
            entries = read_arp_cache()
        self.assertEqual({e.interface_ip for e in entries}, {""})

if __name__ == "__main__":
    unittest.main()