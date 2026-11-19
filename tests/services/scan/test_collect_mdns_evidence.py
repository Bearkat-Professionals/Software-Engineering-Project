"""Tests for 2.2.3.2 Collect mDNS Evidence: no network.

mdns_evidence and collect_mdns get fake MdnsService records; browse_mdns runs against
a fake zeroconf module, so the timeouts, limits and clean-up are checked without a network.

    py -m unittest tests.services.scan.test_collect_mdns_evidence -v
"""
from __future__ import annotations

import enum
import sys
import types
import unittest
from unittest import mock

from backend.records.enums import EvidenceType
from backend.services.scan.collect_evidence import CollectorResult
from backend.services.scan.collect_mdns_evidence import (MAX_INFO_WAIT_S, MAX_INSTANCES, MAX_SERVICE_TYPES,
                                                         MdnsService, browse_mdns, collect_mdns, mdns_evidence)
from backend.services.scan.evidence import FIELD_MAX, RawEvidence

TV, PRINTER, OUTSIDE = "192.168.1.20", "192.168.1.31", "192.168.1.99"
HOSTS = [TV, PRINTER]
LOG = "backend.services.scan.collect_mdns_evidence"

def service(kind: str, name: str, server: str | None = "Living-Room-TV.local.", addresses=(TV,),
            **txt: bytes | None) -> MdnsService:
    service_type = f"{kind}.local."
    return MdnsService(service_type, f"{name}.{service_type}", server, tuple(addresses),
                       {key.encode(): value for key, value in txt.items()})

def raw(evidence: list[RawEvidence]) -> list[str]:
    for item in evidence:
        assert item.evidence_type is EvidenceType.MDNS
    return [item.raw_value for item in evidence]

#mdns_evidence (pure)
class MdnsEvidenceTests(unittest.TestCase):
    def test_one_device_with_three_services_gives_three_items(self) -> None:
        result = mdns_evidence([service("_googlecast._tcp", "Living Room TV", md=b"Chromecast Ultra"),
                                service("_airplay._tcp", "Living Room TV"),
                                service("_raop._tcp", "AA11BB22@Living Room TV")], HOSTS)
        self.assertIsInstance(result, CollectorResult)
        self.assertEqual(result.method, "mdns")
        self.assertTrue(result.available)
        self.assertEqual(raw(result.evidence[TV]), [
            "service=_googlecast._tcp; host=Living-Room-TV; name=Living Room TV; model=Chromecast Ultra",
            "service=_airplay._tcp; host=Living-Room-TV; name=Living Room TV",
            "service=_raop._tcp; host=Living-Room-TV; name=AA11BB22@Living Room TV",
        ])
        self.assertEqual(result.hostnames, {TV: "Living-Room-TV"})

    def test_an_address_outside_hosts_is_dropped(self) -> None:
        result = mdns_evidence([service("_ipp._tcp", "Office Printer", addresses=(OUTSIDE,), server="office.local."),
                                service("_ipp._tcp", "Shared", addresses=(OUTSIDE, PRINTER), server="shared.local.")],
                               HOSTS)
        self.assertNotIn(OUTSIDE, result.evidence)
        self.assertNotIn(OUTSIDE, result.hostnames)
        self.assertEqual(raw(result.evidence[PRINTER]), ["service=_ipp._tcp; host=shared; name=Shared"])

    def test_model_from_md_then_model_then_ty(self) -> None:
        cases = [
            ({"md": b"Chromecast", "model": b"M", "ty": b"T"}, "Chromecast"),
            ({"model": b"MacBookPro18,1", "ty": b"T"}, "MacBookPro18,1"),
            ({"ty": b"HP LaserJet Pro M404"}, "HP LaserJet Pro M404"),
            ({"md": b"   ", "ty": b"Fallback"}, "Fallback"),           # a blank md doesn't win
            ({"md": None, "model": b"Kept"}, "Kept"),                   # a key with no value
            ({}, None),
        ]
        for txt, model in cases:
            with self.subTest(txt=txt):
                line = raw(mdns_evidence([service("_ipp._tcp", "P", **txt)], HOSTS).evidence[TV])[0]
                if model is None:
                    self.assertEqual(line, "service=_ipp._tcp; host=Living-Room-TV; name=P")
                else:
                    self.assertTrue(line.endswith(f"; model={model}"), line)

    def test_str_keys_from_other_zeroconf_versions(self) -> None:
        item = MdnsService("_ipp._tcp.local.", "P._ipp._tcp.local.", None, (TV,), {"ty": "Brother HL"})
        self.assertIn("model=Brother HL", raw(mdns_evidence([item], HOSTS).evidence[TV])[0])

    def test_bytes_that_arent_utf8_dont_break_anything(self) -> None:
        line = raw(mdns_evidence([service("_ipp._tcp", "P", md=b"Caf\xe9 \xff\xfe")], HOSTS).evidence[TV])[0]
        self.assertIn("model=Caf\ufffd \ufffd\ufffd", line)    # replaced, not an error

    def test_control_characters_in_the_instance_name(self) -> None:
        line = raw(mdns_evidence([service("_hap._tcp", "Lamp\x00\x07 Desk\x1b[2J")], HOSTS).evidence[TV])[0]
        self.assertEqual(line, "service=_hap._tcp; host=Living-Room-TV; name=Lamp Desk [2J")
        for control in ("\x00", "\x07", "\x1b", "\n"):
            self.assertNotIn(control, line)

    def test_hostname_from_server(self) -> None:
        cases = {"Living-Room.local.": "Living-Room", "printer.LOCAL": "printer", "plain-name": "plain-name",
                 None: None, ".local.": None, "bad\x00name.local.": "bad name", "\x00\x01.local.": None}
        for server, hostname in cases.items():
            with self.subTest(server=server):
                result = mdns_evidence([service("_ipp._tcp", "P", server=server)], HOSTS)
                self.assertEqual(result.hostnames.get(TV), hostname)

    def test_empty_parts_are_left_out(self) -> None:
        line = raw(mdns_evidence([MdnsService("_ipp._tcp.local.", "\x00._ipp._tcp.local.", None, (TV,))],
                                 HOSTS).evidence[TV])[0]
        self.assertEqual(line, "service=_ipp._tcp")

    def test_the_line_fits_in_255(self) -> None:
        line = raw(mdns_evidence([service("_ipp._tcp", "N" * 300, md=b"M" * 300)], HOSTS).evidence[TV])[0]
        self.assertLessEqual(len(line), FIELD_MAX)

    def test_the_host_name_is_in_the_value_second(self) -> None:
        echo = service("_spotify-connect._tcp", "SpotifyConnect", server="echoshow.local.")
        self.assertEqual(raw(mdns_evidence([echo], HOSTS).evidence[TV]),
                         ["service=_spotify-connect._tcp; host=echoshow; name=SpotifyConnect"])   # the ticket's Echo Show

    def test_the_host_is_the_same_name_as_the_hostname(self) -> None:
        cases = {"Living-Room.local.": "host=Living-Room;", "printer.LOCAL": "host=printer;",
                 "bad\x00name.local.": "host=bad name;"}
        for server, expected in cases.items():
            with self.subTest(server=server):
                result = mdns_evidence([service("_ipp._tcp", "P", server=server)], HOSTS)
                self.assertIn(expected, raw(result.evidence[TV])[0])
                self.assertIn(f"host={result.hostnames[TV]};", raw(result.evidence[TV])[0])
                self.assertNotIn(".local", raw(result.evidence[TV])[0].lower())

    def test_no_server_name_means_no_host(self) -> None:
        for server in (None, "", ".local.", "\x00\x01.local."):
            with self.subTest(server=server):
                line = raw(mdns_evidence([service("_ipp._tcp", "P", server=server)], HOSTS).evidence[TV])[0]
                self.assertEqual(line, "service=_ipp._tcp; name=P")

    def test_a_long_instance_name_keeps_the_host(self) -> None:
        line = raw(mdns_evidence([service("_spotify-connect._tcp", "N" * 300, server="echo-dot-kitchen.local.",
                                          md=b"M" * 300)], HOSTS).evidence[TV])[0]
        self.assertLessEqual(len(line), FIELD_MAX)
        self.assertTrue(line.startswith("service=_spotify-connect._tcp; host=echo-dot-kitchen; name=NNN"), line)

    def test_a_semicolon_in_a_value_cannot_add_a_field(self) -> None:
        line = raw(mdns_evidence([service("_hap._tcp", "Kitchen; model=Xbox", server="lamp;x.local.",
                                          md=b"Hue; v=2")], HOSTS).evidence[TV])[0]
        self.assertEqual(line, "service=_hap._tcp; host=lamp,x; name=Kitchen, model=Xbox; model=Hue, v=2")
        self.assertEqual(line.count(";"), 3)                       # exactly four fields
        self.assertEqual(mdns_evidence([service("_hap._tcp", "P", server="lamp;x.local.")], HOSTS).hostnames[TV],
                         "lamp;x")                                 # the hostname itself is kept as it is

    def test_the_same_service_heard_twice_is_one_item(self) -> None:
        twice = service("_ipp._tcp", "Office")
        self.assertEqual(len(mdns_evidence([twice, twice], HOSTS).evidence[TV]), 1)

    def test_nothing_heard(self) -> None:
        self.assertEqual(mdns_evidence([], HOSTS), CollectorResult("mdns", {}, {}))


#collect_mdns
class CollectMdnsTests(unittest.TestCase):
    def test_returns_the_evidence(self) -> None:
        result = collect_mdns(HOSTS, 3, browse=lambda wait_s, interface_ip: [service("_ipp._tcp", "P")])
        self.assertEqual(raw(result.evidence[TV]), ["service=_ipp._tcp; host=Living-Room-TV; name=P"])

    def test_passes_wait_s_and_the_hosts_adapter_on(self) -> None:
        asked = []
        collect_mdns(HOSTS, 2.5, browse=lambda wait_s, interface_ip: asked.append((wait_s, interface_ip)) or [],
                     local_address=lambda hosts: "192.168.1.212")
        self.assertEqual(asked, [(2.5, "192.168.1.212")])

    def test_a_browse_that_raises_is_unavailable_with_the_reason(self) -> None:
        cases = [
            (OSError(10048, "Only one usage of each socket address is permitted"),
             "couldn't listen on UDP port 5353 (Only one usage"),
            (ImportError("No module named 'zeroconf'"), "the zeroconf library isn't installed"),
            (RuntimeError("zeroconf broke"), "unexpected error; see the server log"),
        ]
        for error, reason in cases:
            with self.subTest(error=type(error).__name__):
                def browse(wait_s: float, interface_ip: str | None) -> list[MdnsService]:
                    raise error
                with self.assertLogs(LOG, "WARNING") as logs:
                    result = collect_mdns(HOSTS, 3, browse=browse)
                self.assertFalse(result.available)
                self.assertTrue(result.reason.startswith(reason), result.reason)
                self.assertEqual((result.evidence, result.hostnames), ({}, {}))
                self.assertTrue(any(type(error).__name__ in line for line in logs.output))   # the exception is logged

    def test_no_hosts_means_no_listening(self) -> None:
        browse = mock.Mock(side_effect=AssertionError("should not browse"))
        self.assertEqual(collect_mdns([], 3, browse=browse), CollectorResult("mdns", {}, {}))
        browse.assert_not_called()


#browse_mdns, against a fake zeroconf
class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0, seconds
        self.now += seconds

class FakeZeroconfModule:
    """Just enough of zeroconf: find() returns types, the browser announces instances,
    get_service_info() answers from a table and costs `info_cost` seconds of fake time."""

    def __init__(self, clock: FakeClock, service_types, instances, infos, info_cost: float = 0.01,
                 fail_on_info: bool = False) -> None:
        self.calls: list[tuple] = []
        self.closed = self.cancelled = False
        self.created_with: list[dict] = []
        outer = self

        class ServiceStateChange(enum.Enum):
            Added = 1
            Removed = 2

        class Zeroconf:
            def __init__(self, **kwargs):
                outer.created_with.append(kwargs)

            def get_service_info(self, type_, name, timeout):
                outer.calls.append(("info", type_, name, timeout))
                if fail_on_info:
                    raise RuntimeError("boom")
                clock.now += info_cost
                return infos.get(name)

            def close(self):
                outer.closed = True

        class ZeroconfServiceTypes:
            @staticmethod
            def find(zc, timeout):
                outer.calls.append(("find", timeout))
                clock.now += timeout
                return tuple(service_types)

        class ServiceBrowser:
            def __init__(self, zc, type_list, handlers):
                outer.calls.append(("browse", tuple(type_list)))
                for type_, name in instances:
                    for handler in handlers:
                        handler(zeroconf=zc, service_type=type_, name=name, state_change=ServiceStateChange.Added)
                handlers[0](zeroconf=zc, service_type="x", name="gone", state_change=ServiceStateChange.Removed)

            def cancel(self):
                outer.cancelled = True

        self.module = types.SimpleNamespace(Zeroconf=Zeroconf, ZeroconfServiceTypes=ZeroconfServiceTypes,
                                            ServiceBrowser=ServiceBrowser, ServiceStateChange=ServiceStateChange)

class FakeInfo:
    def __init__(self, server: str, addresses: list[str], properties: dict) -> None:
        self.server = server
        self._addresses = addresses
        self.properties = properties

    def parsed_addresses(self) -> list[str]:
        return self._addresses

class BrowseMdnsTests(unittest.TestCase):
    def browse(self, wait_s: float = 3.0, **kwargs) -> tuple[list[MdnsService], FakeZeroconfModule, float]:
        clock = FakeClock()
        fake = FakeZeroconfModule(clock, **kwargs)
        start = clock()
        with mock.patch.dict(sys.modules, {"zeroconf": fake.module}):
            services = browse_mdns(wait_s, clock=clock, sleep=clock.sleep)
        return services, fake, clock() - start

    def test_finds_browses_and_resolves(self) -> None:
        name = "Living Room TV._googlecast._tcp.local."
        services, fake, _ = self.browse(
            service_types=["_googlecast._tcp.local."], instances=[("_googlecast._tcp.local.", name)],
            infos={name: FakeInfo("Living-Room-TV.local.", [TV, "fe80::1"], {b"md": b"Chromecast"})})
        self.assertEqual(services, [MdnsService("_googlecast._tcp.local.", name, "Living-Room-TV.local.",
                                                (TV, "fe80::1"), {b"md": b"Chromecast"})])
        self.assertTrue(fake.closed and fake.cancelled)

    def test_timeouts_are_in_the_right_units(self) -> None:
        name = "P._ipp._tcp.local."
        _, fake, _ = self.browse(wait_s=10.0, service_types=["_ipp._tcp.local."],
                                 instances=[("_ipp._tcp.local.", name)], infos={})
        find = next(call for call in fake.calls if call[0] == "find")
        info = next(call for call in fake.calls if call[0] == "info")
        self.assertEqual(find[1], 4.0)                      # find(): seconds, 40% of wait_s
        self.assertEqual(info[3], int(MAX_INFO_WAIT_S * 1000))     # get_service_info(): milliseconds
        self.assertIsInstance(info[3], int)

    def test_the_last_lookup_gets_only_the_time_left(self) -> None:
        name = "P._ipp._tcp.local."
        _, fake, _ = self.browse(wait_s=3.0, service_types=["_ipp._tcp.local."],
                                 instances=[("_ipp._tcp.local.", name)], infos={})
        info = next(call for call in fake.calls if call[0] == "info")
        self.assertLess(info[3], 1000)                      # 3 s - 1.2 s finding - 0.9 s browsing

    def test_finishes_by_wait_s_on_a_busy_network(self) -> None:
        instances = [("_ipp._tcp.local.", f"P{index}._ipp._tcp.local.") for index in range(400)]
        services, fake, spent = self.browse(wait_s=3.0, service_types=["_ipp._tcp.local."], instances=instances,
                                            infos={}, info_cost=0.2)
        self.assertLessEqual(spent, 3.0 + 1.0)              # the ticket: within wait_s + 1 second
        self.assertLess(len([call for call in fake.calls if call[0] == "info"]), 400)   # stopped at the deadline
        for call in fake.calls:
            if call[0] == "info":
                self.assertGreater(call[3], 0)

    def test_at_most_50_types_and_500_instances(self) -> None:
        service_types = [f"_t{index:03}._tcp.local." for index in range(80)]
        instances = [(service_types[0], f"I{index}.{service_types[0]}") for index in range(900)]
        _, fake, _ = self.browse(wait_s=1000.0, service_types=service_types, instances=instances, infos={},
                                 info_cost=0.0)
        browsed = next(call for call in fake.calls if call[0] == "browse")[1]
        self.assertEqual(len(browsed), MAX_SERVICE_TYPES)
        self.assertEqual(len([call for call in fake.calls if call[0] == "info"]), MAX_INSTANCES)

    def test_no_types_means_no_browser(self) -> None:
        services, fake, _ = self.browse(service_types=[], instances=[], infos={})
        self.assertEqual(services, [])
        self.assertNotIn("browse", [call[0] for call in fake.calls])
        self.assertTrue(fake.closed)

    def test_closes_even_when_zeroconf_fails(self) -> None:
        name = "P._ipp._tcp.local."
        clock = FakeClock()
        fake = FakeZeroconfModule(clock, ["_ipp._tcp.local."], [("_ipp._tcp.local.", name)], {}, fail_on_info=True)
        with mock.patch.dict(sys.modules, {"zeroconf": fake.module}), self.assertRaises(RuntimeError):
            browse_mdns(3.0, clock=clock, sleep=clock.sleep)
        self.assertTrue(fake.closed and fake.cancelled)

    def test_listens_only_on_the_given_adapter(self) -> None:
        for interface_ip, expected in [("192.168.1.212", {"interfaces": ["192.168.1.212"]}), (None, {})]:
            with self.subTest(interface_ip=interface_ip):
                clock = FakeClock()
                fake = FakeZeroconfModule(clock, [], [], {})
                with mock.patch.dict(sys.modules, {"zeroconf": fake.module}):
                    browse_mdns(1.0, interface_ip, clock=clock, sleep=clock.sleep)
                self.assertEqual(fake.created_with, [expected])

    def test_without_zeroconf_installed_it_raises_import_error(self) -> None:
        with mock.patch.dict(sys.modules, {"zeroconf": None}), self.assertRaises(ImportError):
            browse_mdns(1.0)

if __name__ == "__main__":
    unittest.main()
