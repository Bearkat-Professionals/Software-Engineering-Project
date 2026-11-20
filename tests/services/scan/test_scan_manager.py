"""Unit tests for the Scan Manager (2.2, #48). No network, no database: the real host
discovery runs on FakeNetwork and FakeArpCache, and the real session controller on
FakeScanStore.

    python -m unittest discover tests
"""
from __future__ import annotations

import threading
import unittest
from collections.abc import Callable
from datetime import datetime
from typing import Any
from unittest import mock

from backend.black_boxes.read_arp_cache import ArpEntry, ArpEntryType
from backend.black_boxes.read_gateway_config import Gateway
from backend.data_access.reference_data import ReferenceRules
from backend.records import Vendor
from backend.records.enums import AlertEventName, EvidenceType, ScanOutcome, ScanStatus
from backend.services.scan.collect_evidence import NO_EVIDENCE, CollectedEvidence, CollectorResult
from backend.services.scan.collect_mdns_evidence import collect_mdns
from backend.services.scan.collect_name_evidence import collect_netbios, collect_reverse_dns
from backend.services.scan.collect_ssdp_evidence import collect_ssdp
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.evidence import RawEvidence
from backend.services.scan.record_scan_results import RecordSummary
from backend.services.scan.identification_manager import IdentificationRules
from backend.services.scan.scan_manager import (EVIDENCE_COLLECTORS, AlertCandidate, ScanManager, ScanResult,
                                                evidence_collectors)
from backend.services.scan.scan_session import ScanPhase, ScanSessionController
from backend.services.scan.scan_settings import ScanSettings
from tests.fakes.fake_arp_cache import FakeArpCache
from tests.fakes.fake_network import FakeNetwork
from tests.services.scan.test_scan_session import JAVIER, JASON, FakeScanStore, FakeUserStore

SCOPE = "192.168.1.0/30"                       # two host addresses: .1 and .2
ROUTER_IP, ROUTER_MAC = "192.168.1.1", "70:13:01:65:8E:F7"
LAPTOP_IP, LAPTOP_MAC = "192.168.1.2", "00:1B:44:11:3A:B7"
NOW = datetime(2026, 11, 5, 18, 30, 15, 123456)
VANTIVA = Vendor(vendor_id=2, vendor_name="Vantiva - Connected Home")
RULES = ReferenceRules(vendors={2: VANTIVA}, prefixes={"70:13:01": 2}, device_types={}, signatures=())


def arp_entry(ip: str, mac: str) -> ArpEntry:
    return ArpEntry(ip, mac, "192.168.1.3", ArpEntryType.DYNAMIC)


class FakeRecorder:
    """Stands in for record_scan_results (with engine, saver and scans filled in)."""
    def __init__(self, reject: set[str] | None = None, crash: Exception | None = None) -> None:
        self.reject = reject or set()
        self.crash = crash
        self.calls: list[dict[str, Any]] = []

    def __call__(self, scan_id: int, user_id: int, hosts: list[DiscoveredHost], rules: IdentificationRules,
                 seen_at: datetime, *, on_host_done: Callable[[int, int], None] | None = None,
                 collected: CollectedEvidence = NO_EVIDENCE) -> RecordSummary:
        self.calls.append({"scan_id": scan_id, "user_id": user_id, "hosts": list(hosts),
                           "rules": rules, "seen_at": seen_at, "collected": collected})
        if self.crash is not None:
            raise self.crash
        to_save = [host for host in hosts if host.mac_address]
        summary = RecordSummary(skipped_no_mac=len(hosts) - len(to_save))
        if on_host_done:
            on_host_done(0, len(to_save))
        for done, host in enumerate(to_save, start=1):
            if host.ip_address in self.reject:
                summary.failures.append((host.ip_address, "rejected by SH-4"))
            else:
                summary.created += 1
                summary.new_flagged_ids.append(100 + done)
            if on_host_done:
                on_host_done(done, len(to_save))
        return summary


class ManagerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeScanStore()
        self.sessions = ScanSessionController(self.store, FakeUserStore())
        self.network = FakeNetwork(responding={ROUTER_IP})
        self.arp = FakeArpCache([arp_entry(ROUTER_IP, ROUTER_MAC), arp_entry(LAPTOP_IP, LAPTOP_MAC)])
        self.recorder = FakeRecorder()
        self.finished: list[ScanResult] = []

    def manager(self, **overrides: Any) -> ScanManager:
        options: dict[str, Any] = dict(
            load_rules=lambda: RULES, record_hosts=self.recorder, settings=ScanSettings(),
            probe=self.network.probe, read_arp=self.arp.read_arp_cache, clock=lambda: NOW,
            on_finished=self.finished.append)
        options.update(overrides)
        return ScanManager(self.sessions, **options)

    def run_scan(self, **overrides: Any) -> ScanResult:
        manager = self.manager(**overrides)
        opened = self.sessions.open(SCOPE, JAVIER.user_id, manager.settings)
        self.assertTrue(opened.opened, opened.reason)
        return manager.run(opened.scan)

    def stored(self, result: ScanResult):
        return self.store.scans[result.scan_id]


class FullScanTests(ManagerTestCase):
    def test_hosts_are_found_saved_and_the_scan_succeeds(self) -> None:
        result = self.run_scan()
        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        self.assertTrue(result.closed)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.summary.created, 2)
        [call] = self.recorder.calls
        self.assertEqual({(h.ip_address, h.mac_address) for h in call["hosts"]},
                         {(ROUTER_IP, ROUTER_MAC), (LAPTOP_IP, LAPTOP_MAC)})
        stored = self.stored(result)
        self.assertEqual((stored.status, stored.outcome, stored.error_message),
                         (ScanStatus.COMPLETED, ScanOutcome.SUCCESS, None))

    def test_phases_pass_their_output_on(self) -> None:
        result = self.run_scan()
        [call] = self.recorder.calls
        self.assertEqual((call["scan_id"], call["user_id"]), (result.scan_id, JAVIER.user_id))
        self.assertEqual(call["rules"].oui_lookup, {"70:13:01": (2, "Vantiva - Connected Home")})   # from the rules
        self.assertEqual(call["seen_at"], NOW.replace(microsecond=0))

    def test_gateway_is_saved_with_the_mac_from_discovered_hosts(self) -> None:
        gateway = mock.Mock(return_value=Gateway(ROUTER_IP, "192.168.1.3"))
        with mock.patch("backend.services.scan.scan_manager.local_address_for", return_value="192.168.1.3") as local:
            result = self.run_scan(read_gateway=gateway)
        local.assert_called_once_with([ROUTER_IP, LAPTOP_IP])
        gateway.assert_called_once_with("192.168.1.3")
        stored = self.stored(result)
        self.assertEqual((stored.gateway_ip, stored.gateway_mac), (ROUTER_IP, ROUTER_MAC))
        self.assertNotIn(ROUTER_IP, self.arp.calls)       # the host already carried its MAC

    def test_gateway_mac_falls_back_to_a_targeted_arp_read(self) -> None:
        gateway_ip, gateway_mac = "192.168.1.254", "28:6F:B9:10:20:30"

        def read_arp(ip: str | None = None) -> list[ArpEntry]:
            self.arp.calls.append(ip)
            if ip == gateway_ip:
                return [arp_entry(gateway_ip, gateway_mac)]
            return list(self.arp.entries)

        result = self.run_scan(read_arp=read_arp,
                               read_gateway=lambda local_ip: Gateway(gateway_ip, local_ip or "unknown"))
        self.assertEqual((self.stored(result).gateway_ip, self.stored(result).gateway_mac),
                         (gateway_ip, gateway_mac))
        self.assertIn(gateway_ip, self.arp.calls)

    def test_no_gateway_is_normal_and_the_scan_still_succeeds(self) -> None:
        result = self.run_scan(read_gateway=lambda local_ip: None)
        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        self.assertEqual((self.stored(result).gateway_ip, self.stored(result).gateway_mac), (None, None))

    def test_gateway_reader_failure_does_not_fail_the_scan(self) -> None:
        def broken_gateway(local_ip: str | None) -> Gateway | None:
            raise OSError("route table unavailable")

        with self.assertLogs("backend.services.scan.scan_manager", "WARNING"):
            result = self.run_scan(read_gateway=broken_gateway)

        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        self.assertEqual(result.summary.created, 2)
        self.assertEqual(result.errors, ())

    def test_gateway_database_failure_does_not_fail_the_scan(self) -> None:
        with mock.patch.object(self.store, "set_gateway", side_effect=ConnectionError("database went away")):
            with self.assertLogs("backend.services.scan.scan_manager", "WARNING"):
                result = self.run_scan(read_gateway=lambda local_ip: Gateway(ROUTER_IP, local_ip or "unknown"))

        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        self.assertEqual(result.summary.created, 2)
        self.assertEqual(result.errors, ())

    def test_new_flagged_devices_become_alert_candidates(self) -> None:
        result = self.run_scan()
        self.assertEqual(result.alerts, (AlertCandidate(AlertEventName.NEW_UNIDENTIFIED_DEVICE, 101),
                                         AlertCandidate(AlertEventName.NEW_UNIDENTIFIED_DEVICE, 102)))
        self.assertEqual(self.finished, [result])

    def test_progress_goes_through_every_phase_in_order(self) -> None:
        with mock.patch.object(self.sessions, "report_progress", wraps=self.sessions.report_progress) as seen:
            self.run_scan()
        calls = [c.args for c in seen.call_args_list]
        phases = [phase for phase, _, _ in calls]
        self.assertEqual(sorted(set(phases), key=phases.index),
                         [ScanPhase.DISCOVERING_HOSTS, ScanPhase.SAVING_RESULTS, ScanPhase.CLOSING])
        self.assertIn((ScanPhase.DISCOVERING_HOSTS, 2, 2), calls)        # both addresses probed
        self.assertIn((ScanPhase.SAVING_RESULTS, 2, 2), calls)           # both hosts saved


class UnavailableMethodTests(ManagerTestCase):
    def test_ping_unavailable_completes_from_the_arp_cache_as_partial(self) -> None:
        result = self.run_scan(probe=FakeNetwork(unavailable=True).probe)
        self.assertEqual(result.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(result.summary.created, 2)                      # both found in the ARP cache
        self.assertEqual(result.errors, ("ping unavailable: no address could be probed",))
        self.assertEqual(self.stored(result).error_message, "ping unavailable: no address could be probed")

    def test_arp_unavailable_is_partial(self) -> None:
        result = self.run_scan(read_arp=FakeArpCache(unavailable=True).read_arp_cache)
        self.assertEqual(result.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(result.summary.skipped_no_mac, 1)              # the router answered, but no MAC
        self.assertIn("ARP cache unavailable: MAC addresses could not be read", result.errors)

    def test_both_unavailable_fails_without_saving(self) -> None:
        result = self.run_scan(probe=FakeNetwork(unavailable=True).probe,
                               read_arp=FakeArpCache(unavailable=True).read_arp_cache)
        self.assertEqual(result.outcome, ScanOutcome.FAILED)
        self.assertIsNone(result.summary)
        self.assertEqual(self.recorder.calls, [])
        self.assertTrue(result.errors[0].startswith("no host discovery method worked"))
        self.assertEqual(len(result.errors), 3)                         # the reason, then both methods
        self.assertEqual(self.finished[0].alerts, (AlertCandidate(AlertEventName.SCAN_FAILED),))

    def test_a_turned_off_method_is_not_unavailable(self) -> None:
        result = self.run_scan(settings=ScanSettings(discovery_methods=("arp",)))
        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        self.assertEqual(self.network.probed, [])                       # no ping at all
        self.assertEqual(result.errors, ())

    def test_a_host_that_cannot_be_saved_makes_it_partial(self) -> None:
        self.recorder.reject = {LAPTOP_IP}
        result = self.run_scan()
        self.assertEqual(result.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(result.errors, (f"{LAPTOP_IP}: rejected by SH-4",))


class CrashTests(ManagerTestCase):
    def test_exception_while_saving_fails_closes_and_lets_the_next_scan_start(self) -> None:
        self.recorder.crash = ConnectionError("database went away")
        with self.assertLogs("backend.services.scan.scan_manager", "ERROR"):
            result = self.run_scan()
        self.assertEqual(result.outcome, ScanOutcome.FAILED)
        self.assertEqual(result.errors[0], "scan stopped: ConnectionError: database went away")
        self.assertIs(self.stored(result).status, ScanStatus.COMPLETED)
        self.assertTrue(self.sessions.open(SCOPE, JASON.user_id, ScanSettings()).opened)

    def test_exception_while_loading_reference_data_fails(self) -> None:
        def broken() -> ReferenceRules:
            raise RuntimeError("no reference data")
        with self.assertLogs("backend.services.scan.scan_manager", "ERROR"):
            result = self.run_scan(load_rules=broken)
        self.assertEqual(result.outcome, ScanOutcome.FAILED)
        self.assertEqual(self.network.probed, [])                       # stopped before discovery

    def test_close_that_cannot_be_saved_skips_on_finished(self) -> None:
        self.store.failing_finishes = 1
        with self.assertLogs("backend.services.scan.scan_manager", "ERROR"):
            result = self.run_scan()
        self.assertFalse(result.closed)
        self.assertEqual(self.finished, [])                             # nothing committed, no alerts
        self.assertTrue(self.sessions.open(SCOPE, JASON.user_id, ScanSettings()).opened)   # close retried
        self.assertEqual(self.stored(result).outcome, ScanOutcome.SUCCESS)

    def test_on_finished_error_is_logged_and_the_scan_stays_saved(self) -> None:
        def broken(result: ScanResult) -> None:
            raise RuntimeError("mail server down")
        with self.assertLogs("backend.services.scan.scan_manager", "ERROR"):
            result = self.run_scan(on_finished=broken)
        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        self.assertIs(self.stored(result).status, ScanStatus.COMPLETED)


class BackgroundTests(ManagerTestCase):
    def test_start_returns_before_the_scan_finishes(self) -> None:
        gate = threading.Event()

        def waiting_recorder(*args: Any, **kwargs: Any) -> RecordSummary:
            gate.wait(timeout=5)
            return self.recorder(*args, **kwargs)

        manager = self.manager(record_hosts=waiting_recorder)
        opened = manager.start(SCOPE, JAVIER.user_id)
        self.assertTrue(opened.opened)
        self.assertEqual(self.sessions.status().state, "running")
        gate.set()
        self.assertTrue(manager.wait(timeout=5))
        report = self.sessions.status()
        self.assertEqual((report.state, report.outcome), ("finished", ScanOutcome.SUCCESS))
        self.assertEqual(len(self.finished), 1)

    def test_a_refused_start_starts_nothing(self) -> None:
        self.sessions.open(SCOPE, JASON.user_id, ScanSettings())
        manager = self.manager()
        refused = manager.start(SCOPE, JAVIER.user_id)
        self.assertFalse(refused.opened)
        self.assertIn("Jason", refused.reason)
        self.assertTrue(manager.wait(timeout=0))                        # no thread was started
        self.assertEqual(self.network.probed, [])


#2.2.3 the evidence phase (#151), with fake collectors

ROUTER_SSDP = RawEvidence(EvidenceType.SSDP, "manufacturer=Vantiva; model=CGM4331")
LAPTOP_MDNS = RawEvidence(EvidenceType.MDNS, "service=_workstation._tcp; name=javier-laptop")


def fake_collector(method: str, evidence=None, hostnames=None, available=True, reason=None):
    asked = []

    def collector(hosts, wait_s):
        asked.append((sorted(hosts), wait_s))
        return CollectorResult(method, evidence or {}, hostnames or {}, available, reason)
    collector.asked = asked
    return collector


class EvidencePhaseTests(ManagerTestCase):
    def collectors(self, **overrides):
        found = {"mdns": fake_collector("mdns", {LAPTOP_IP: [LAPTOP_MDNS]}, {LAPTOP_IP: "javier-laptop"}),
                 "ssdp": fake_collector("ssdp", {ROUTER_IP: [ROUTER_SSDP]})}
        found.update(overrides)
        return found

    def test_evidence_and_hostnames_reach_the_recorder(self) -> None:
        collectors = self.collectors()
        result = self.run_scan(collectors=collectors, settings=ScanSettings(evidence_wait_s=2))
        self.assertEqual(result.outcome, ScanOutcome.SUCCESS)
        [call] = self.recorder.calls
        self.assertEqual(call["collected"].evidence, {ROUTER_IP: (ROUTER_SSDP,), LAPTOP_IP: (LAPTOP_MDNS,)})
        self.assertEqual(call["collected"].hostnames, {LAPTOP_IP: "javier-laptop"})
        self.assertEqual(collectors["mdns"].asked, [([ROUTER_IP, LAPTOP_IP], 2.0)])     # the hosts found, wait_s

    def test_the_phase_is_reported_between_discovery_and_saving(self) -> None:
        with mock.patch.object(self.sessions, "report_progress", wraps=self.sessions.report_progress) as seen:
            self.run_scan(collectors=self.collectors())
        calls = [c.args for c in seen.call_args_list]
        phases = [phase for phase, _, _ in calls]
        self.assertEqual(sorted(set(phases), key=phases.index),
                         [ScanPhase.DISCOVERING_HOSTS, ScanPhase.COLLECTING_EVIDENCE,
                          ScanPhase.SAVING_RESULTS, ScanPhase.CLOSING])
        evidence = [(done, total) for phase, done, total in calls if phase is ScanPhase.COLLECTING_EVIDENCE]
        self.assertEqual(evidence, [(0, 2), (1, 2), (2, 2)])                # one step per method

    def test_an_unavailable_method_is_an_error_line_and_partial(self) -> None:
        collectors = self.collectors(mdns=fake_collector("mdns", available=False,
                                                         reason="couldn't listen on UDP port 5353 (in use)"))
        result = self.run_scan(collectors=collectors)
        self.assertEqual(result.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(result.errors, ("mdns unavailable: couldn't listen on UDP port 5353 (in use)",))
        self.assertEqual(self.recorder.calls[0]["collected"].evidence, {ROUTER_IP: (ROUTER_SSDP,)})   # ssdp kept

    def test_a_crashing_collector_is_unavailable_not_a_failed_scan(self) -> None:
        def crashing(hosts, wait_s):
            raise OSError(10048, "Only one usage of each socket address")
        with self.assertLogs("backend.services.scan.collect_evidence", "WARNING"):
            result = self.run_scan(collectors=self.collectors(mdns=crashing))
        self.assertEqual(result.outcome, ScanOutcome.PARTIAL)
        self.assertEqual(result.errors, ("mdns unavailable: OSError",))
        self.assertEqual(result.summary.created, 2)                       # the scan still saved everything

    def test_all_methods_off_means_no_phase_no_error_and_success(self) -> None:
        collectors = self.collectors()
        with mock.patch.object(self.sessions, "report_progress", wraps=self.sessions.report_progress) as seen:
            result = self.run_scan(collectors=collectors, settings=ScanSettings(evidence_methods=()))
        self.assertEqual((result.outcome, result.errors), (ScanOutcome.SUCCESS, ()))
        self.assertNotIn(ScanPhase.COLLECTING_EVIDENCE, [c.args[0] for c in seen.call_args_list])
        self.assertEqual(collectors["mdns"].asked, [])
        self.assertIs(self.recorder.calls[0]["collected"], NO_EVIDENCE)

    def test_only_the_methods_turned_on_run(self) -> None:
        collectors = self.collectors()
        self.run_scan(collectors=collectors, settings=ScanSettings(evidence_methods=("ssdp",)))
        self.assertEqual(collectors["mdns"].asked, [])
        self.assertEqual(len(collectors["ssdp"].asked), 1)

    def test_a_method_turned_on_without_a_collector_is_skipped_not_unavailable(self) -> None:
        result = self.run_scan(collectors={"ssdp": fake_collector("ssdp")})        # the others have no collector here
        self.assertEqual((result.outcome, result.errors), (ScanOutcome.SUCCESS, ()))

    def test_no_hosts_found_asks_nothing(self) -> None:
        collectors = self.collectors()
        self.network = FakeNetwork(responding=set())
        self.arp = FakeArpCache([])
        self.run_scan(collectors=collectors, probe=self.network.probe, read_arp=self.arp.read_arp_cache)
        self.assertEqual(collectors["mdns"].asked, [])

    def test_the_real_collectors_follow_the_settings(self) -> None:
        self.assertEqual(list(evidence_collectors(ScanSettings(evidence_methods=("ssdp", "netbios")))),
                         ["ssdp", "netbios"])
        self.assertEqual(evidence_collectors(ScanSettings(evidence_methods=())), {})

    def test_all_four_evidence_methods_are_built_and_none_skipped(self) -> None:     # #148
        methods = ["mdns", "ssdp", "netbios", "reverse_dns"]
        self.assertEqual(set(EVIDENCE_COLLECTORS), set(methods))
        with self.assertNoLogs("backend.services.scan.scan_manager", level="INFO"):    # no "skipped" line
            built = evidence_collectors(ScanSettings(evidence_methods=tuple(methods)))
        self.assertEqual(list(built), methods)
        self.assertEqual(built, {"mdns": collect_mdns, "ssdp": collect_ssdp,
                                 "netbios": collect_netbios, "reverse_dns": collect_reverse_dns})
        self.assertEqual(list(evidence_collectors(ScanSettings())), list(ScanSettings().evidence_methods))


if __name__ == "__main__":
    unittest.main()
