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
from backend.data_access.reference_data import ReferenceRules
from backend.records import Vendor
from backend.records.enums import AlertEventName, ScanOutcome, ScanStatus
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.record_scan_results import RecordSummary
from backend.services.scan.scan_manager import AlertCandidate, ScanManager, ScanResult
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

    def __call__(self, scan_id: int, user_id: int, hosts: list[DiscoveredHost], lookup: dict, seen_at: datetime,
                 *, on_host_done: Callable[[int, int], None] | None = None) -> RecordSummary:
        self.calls.append({"scan_id": scan_id, "user_id": user_id, "hosts": list(hosts),
                           "lookup": lookup, "seen_at": seen_at})
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
        self.assertEqual(call["lookup"], {"70:13:01": (2, "Vantiva - Connected Home")})   # from the rules
        self.assertEqual(call["seen_at"], NOW.replace(microsecond=0))

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


if __name__ == "__main__":
    unittest.main()
