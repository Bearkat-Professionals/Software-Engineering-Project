"""Tests for 2.2.3 Evidence Collection Manager (#151): fake collectors, no network.

    py -m unittest tests.services.scan.test_collect_evidence -v
"""
from __future__ import annotations

import threading
import time
import unittest

from backend.records.enums import EvidenceType
from backend.services.scan.collect_evidence import (HOSTNAME_ORDER, LATE_REASON, NO_EVIDENCE, CollectedEvidence,
                                                    CollectorResult, collect_evidence)
from backend.services.scan.evidence import RawEvidence

TV, PRINTER, OUTSIDE = "192.168.1.20", "192.168.1.31", "10.9.9.9"
HOSTS = [TV, PRINTER]
LOG = "backend.services.scan.collect_evidence"

MDNS_TV = RawEvidence(EvidenceType.MDNS, "service=_googlecast._tcp; name=Living Room TV")
SSDP_TV = RawEvidence(EvidenceType.SSDP, "manufacturer=Google; model=Chromecast")
DNS_PRINTER = RawEvidence(EvidenceType.REVERSE_DNS, "printer.lan")

def fixed(method: str, evidence=None, hostnames=None, available=True, reason=None):
    """A collector that returns the same result every time, and records what it was asked."""
    asked = []

    def collector(hosts, wait_s):
        asked.append((list(hosts), wait_s))
        return CollectorResult(method, evidence or {}, hostnames or {}, available, reason)
    collector.asked = asked
    return collector

def raising(error: Exception):
    def collector(hosts, wait_s):
        raise error
    return collector

class MergeTests(unittest.TestCase):

    def test_each_hosts_evidence_from_every_collector(self) -> None:
        collected = collect_evidence(HOSTS, {
            "mdns": fixed("mdns", {TV: [MDNS_TV]}),
            "ssdp": fixed("ssdp", {TV: [SSDP_TV]}),
            "reverse_dns": fixed("reverse_dns", {PRINTER: [DNS_PRINTER]}),
        }, wait_s=3)
        self.assertIsInstance(collected, CollectedEvidence)
        self.assertEqual(collected.evidence, {TV: (MDNS_TV, SSDP_TV), PRINTER: (DNS_PRINTER,)})
        self.assertEqual(collected.unavailable, ())

    def test_every_collector_gets_the_hosts_and_wait_s(self) -> None:
        mdns, ssdp = fixed("mdns"), fixed("ssdp")
        collect_evidence(HOSTS, {"mdns": mdns, "ssdp": ssdp}, wait_s=2.5)
        self.assertEqual(mdns.asked, [(HOSTS, 2.5)])
        self.assertEqual(ssdp.asked, [(HOSTS, 2.5)])

    def test_the_same_value_twice_is_kept_once(self) -> None:
        collected = collect_evidence(HOSTS, {
            "mdns": fixed("mdns", {TV: [MDNS_TV, MDNS_TV]}),
            "mdns_again": fixed("mdns", {TV: [MDNS_TV]}),
        }, wait_s=1)
        self.assertEqual(collected.evidence[TV], (MDNS_TV,))

    def test_addresses_outside_the_scan_are_dropped(self) -> None:
        collected = collect_evidence(HOSTS, {"mdns": fixed("mdns", {OUTSIDE: [MDNS_TV]}, {OUTSIDE: "elsewhere"})},
                                     wait_s=1)
        self.assertEqual((collected.evidence, collected.hostnames), ({}, {}))

    def test_values_are_cleaned_and_empty_ones_dropped(self) -> None:
        collected = collect_evidence(HOSTS, {"mdns": fixed("mdns", {
            TV: [RawEvidence(EvidenceType.MDNS, "  name=Den\r\nTV "), RawEvidence(EvidenceType.MDNS, "\x00 ")]},
            {TV: "\tden-tv\n"})}, wait_s=1)
        self.assertEqual(collected.evidence[TV], (RawEvidence(EvidenceType.MDNS, "name=Den TV"),))
        self.assertEqual(collected.hostnames[TV], "den-tv")

    def test_no_hosts_or_no_collectors_runs_nothing(self) -> None:
        mdns = fixed("mdns")
        self.assertEqual(collect_evidence([], {"mdns": mdns}, wait_s=1), NO_EVIDENCE)
        self.assertEqual(collect_evidence(HOSTS, {}, wait_s=1), NO_EVIDENCE)
        self.assertEqual(mdns.asked, [])

class HostnameTests(unittest.TestCase):

    def test_reverse_dns_beats_mdns_beats_netbios(self) -> None:
        self.assertEqual(HOSTNAME_ORDER, ("reverse_dns", "mdns", "netbios"))
        everyone = {"netbios": fixed("netbios", hostnames={TV: "NETBIOS-TV", PRINTER: "PRN01"}),
                    "mdns": fixed("mdns", hostnames={TV: "Living-Room-TV", PRINTER: "printer-mdns"}),
                    "reverse_dns": fixed("reverse_dns", hostnames={TV: "tv.lan"})}
        collected = collect_evidence(HOSTS, everyone, wait_s=1)
        self.assertEqual(collected.hostnames, {TV: "tv.lan", PRINTER: "printer-mdns"})

        without_dns = {key: value for key, value in everyone.items() if key != "reverse_dns"}
        self.assertEqual(collect_evidence(HOSTS, without_dns, wait_s=1).hostnames,
                         {TV: "Living-Room-TV", PRINTER: "printer-mdns"})
        only_netbios = {"netbios": everyone["netbios"]}
        self.assertEqual(collect_evidence(HOSTS, only_netbios, wait_s=1).hostnames[TV], "NETBIOS-TV")

    def test_the_methods_key_decides_not_what_the_result_says(self) -> None:
        collected = collect_evidence(HOSTS, {"netbios": fixed("reverse_dns", hostnames={TV: "from-netbios"}),
                                             "mdns": fixed("mdns", hostnames={TV: "from-mdns"})}, wait_s=1)
        self.assertEqual(collected.hostnames[TV], "from-mdns")

    def test_a_blank_name_doesnt_win(self) -> None:
        collected = collect_evidence(HOSTS, {"reverse_dns": fixed("reverse_dns", hostnames={TV: "   "}),
                                             "mdns": fixed("mdns", hostnames={TV: "den-tv"})}, wait_s=1)
        self.assertEqual(collected.hostnames[TV], "den-tv")

class UnavailableTests(unittest.TestCase):

    def test_a_raising_collector_is_unavailable_and_the_others_are_kept(self) -> None:
        with self.assertLogs(LOG, "WARNING") as logs:
            collected = collect_evidence(HOSTS, {"mdns": raising(OSError(10048, "port in use")),
                                                 "ssdp": fixed("ssdp", {TV: [SSDP_TV]})}, wait_s=1)
        self.assertEqual(collected.unavailable, (("mdns", "OSError"),))
        self.assertEqual(collected.evidence, {TV: (SSDP_TV,)})
        self.assertTrue(any("Traceback" in line for line in logs.output))     

    def test_a_collector_that_says_it_couldnt_run(self) -> None:
        collected = collect_evidence(HOSTS, {"ssdp": fixed("ssdp", {TV: [SSDP_TV]}, available=False,
                                                           reason="the SSDP search couldn't be sent")}, wait_s=1)
        self.assertEqual(collected.unavailable, (("ssdp", "the SSDP search couldn't be sent"),))
        self.assertEqual(collected.evidence, {})                  

    def test_a_collector_returning_something_else(self) -> None:
        collected = collect_evidence(HOSTS, {"mdns": lambda hosts, wait_s: None}, wait_s=1)
        self.assertEqual(collected.unavailable, (("mdns", "returned no result"),))

    def test_unavailable_methods_are_in_the_order_given(self) -> None:
        collected = collect_evidence(HOSTS, {"ssdp": raising(RuntimeError()), "mdns": raising(ValueError()),
                                             "netbios": fixed("netbios")}, wait_s=1)
        self.assertEqual(collected.unavailable, (("ssdp", "RuntimeError"), ("mdns", "ValueError")))

    def test_a_hanging_collector_is_given_up_on_after_the_grace_time(self) -> None:
        release = threading.Event()
        late_result = []

        def hanging(hosts, wait_s):
            release.wait(5)
            late_result.append("returned")
            return CollectorResult("mdns", {TV: [MDNS_TV]}, {TV: "late"})

        started = time.monotonic()
        with self.assertLogs(LOG, "WARNING"):
            collected = collect_evidence(HOSTS, {"mdns": hanging, "ssdp": fixed("ssdp", {TV: [SSDP_TV]})},
                                         wait_s=0.1, grace_s=0.2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(collected.unavailable, (("mdns", LATE_REASON),))
        self.assertEqual(collected.evidence, {TV: (SSDP_TV,)})
        self.assertEqual(collected.hostnames, {})

        release.set()                                          
        time.sleep(0.05)
        self.assertEqual(late_result, ["returned"])
        self.assertEqual(collected.evidence, {TV: (SSDP_TV,)})

class ProgressTests(unittest.TestCase):

    def test_on_done_after_each_collector(self) -> None:
        calls = []
        collect_evidence(HOSTS, {"mdns": fixed("mdns"), "ssdp": raising(OSError()), "netbios": fixed("netbios")},
                         wait_s=1, on_done=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])

    def test_a_late_collector_still_completes_the_count(self) -> None:
        calls = []
        never = threading.Event()
        collect_evidence(HOSTS, {"mdns": lambda hosts, wait_s: never.wait(2),
                                 "ssdp": fixed("ssdp")},
                         wait_s=0.05, on_done=lambda done, total: calls.append((done, total)), grace_s=0.1)
        never.set()
        self.assertEqual(calls, [(1, 2), (2, 2)])

    def test_a_broken_progress_callback_loses_nothing(self) -> None:
        def broken(done, total):
            raise RuntimeError("screen gone")
        with self.assertLogs(LOG, "ERROR"):
            collected = collect_evidence(HOSTS, {"mdns": fixed("mdns", {TV: [MDNS_TV]})}, wait_s=1, on_done=broken)
        self.assertEqual(collected.evidence, {TV: (MDNS_TV,)})

    def test_collectors_run_at_the_same_time(self) -> None:
        def slow(hosts, wait_s):
            time.sleep(0.3)
            return CollectorResult("x", {}, {})
        started = time.monotonic()
        collect_evidence(HOSTS, {name: slow for name in ("mdns", "ssdp", "netbios", "reverse_dns")}, wait_s=1)
        self.assertLess(time.monotonic() - started, 0.9)         

if __name__ == "__main__":
    unittest.main()