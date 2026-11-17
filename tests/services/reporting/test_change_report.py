"""Tests for 2.4.3 Change Report (#153). No database: build_change_report gets rows
made here, and the service gets a fake ScanStore.

    py -m unittest tests.services.reporting.test_change_report -v
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from backend.data_access.network_data import ComparedObservation, FinishedScan, ScanPage
from backend.records import Scan
from backend.records.enums import ScanOutcome, ScanStatus
from backend.services.records.reference_maintenance import MaintenanceStatus
from backend.services.reporting.change_report import (ChangeKind, ChangeReportService, build_change_report,
                                                      is_comparable)

MONDAY, TUESDAY = datetime(2026, 11, 16, 9), datetime(2026, 11, 17, 9)

def scan(scan_id: int, started_at: datetime, scope: str = "192.168.1.0/24",
         outcome: ScanOutcome | None = ScanOutcome.SUCCESS,
         status: ScanStatus = ScanStatus.COMPLETED) -> Scan:
    return Scan(scan_id=scan_id, user_id=1, target_scope=scope, started_at=started_at,
                status=status, outcome=outcome)

def seen(scan_id: int, device_id: int, ip: str | None, hostname: str | None = None,
         randomized: bool = False, first_seen: datetime = datetime(2026, 1, 1)) -> ComparedObservation:
    return ComparedObservation(scan_id=scan_id, device_id=device_id, ip_address=ip, hostname=hostname,
                               device_name=f"device {device_id}", mac_address=f"F0:D1:A9:00:00:{device_id:02X}",
                               is_randomized_mac=randomized, first_seen=first_seen,
                               vendor_name="Acme", device_type_name="printer")

FROM, TO = scan(1, MONDAY), scan(2, TUESDAY)

def ids(devices) -> list[int]:
    return [device.device_id for device in devices]

#build_change_report (pure)
class BuildChangeReportTests(unittest.TestCase):

    def test_each_kind(self) -> None:
        report = build_change_report(FROM, TO, [
            seen(1, 10, "192.168.1.10"),                                   # gone
            seen(1, 11, "192.168.1.11", "tv"), seen(2, 11, "192.168.1.12", "tv"),   # moved
            seen(2, 12, "192.168.1.13"),                                   # arrived
        ])
        self.assertEqual((ids(report.new), ids(report.missing), ids(report.changed)), ([12], [10], [11]))
        [moved] = report.changed
        self.assertEqual((moved.old_ip, moved.new_ip), ("192.168.1.11", "192.168.1.12"))
        [gone] = report.missing
        self.assertEqual((gone.old_ip, gone.new_ip), ("192.168.1.10", None))
        [arrived] = report.new
        self.assertEqual((arrived.old_ip, arrived.new_ip), (None, "192.168.1.13"))
        self.assertEqual((arrived.device_name, arrived.vendor_name, arrived.device_type_name),
                         ("device 12", "Acme", "printer"))

    def test_the_two_scans_are_described(self) -> None:
        report = build_change_report(FROM, TO, [])
        self.assertEqual((report.from_scan.scan_id, report.from_scan.started_at, report.from_scan.target_scope,
                          report.from_scan.outcome), (1, MONDAY, "192.168.1.0/24", ScanOutcome.SUCCESS))
        self.assertEqual(report.to_scan.scan_id, 2)

    def test_a_hostname_change_alone_is_a_change(self) -> None:
        report = build_change_report(FROM, TO, [seen(1, 11, "192.168.1.11", "printer"),
                                                seen(2, 11, "192.168.1.11", "office-printer")])
        [changed] = report.changed
        self.assertEqual((changed.old_hostname, changed.new_hostname), ("printer", "office-printer"))

    def test_nothing_different_is_not_listed(self) -> None:
        report = build_change_report(FROM, TO, [
            seen(1, 11, "192.168.1.11", "tv"), seen(2, 11, "192.168.1.11", "tv"),
            seen(1, 12, "192.168.1.12", "Den-TV"), seen(2, 12, "192.168.1.12", "den-tv"),   # case only
            seen(1, 13, "192.168.1.13", None), seen(2, 13, "192.168.1.13", ""),            # no name either way
        ])
        self.assertEqual((report.new, report.missing, report.changed), ((), (), ()))

    def test_missing_only_inside_the_later_scans_range(self) -> None:
        narrower = scan(2, TUESDAY, scope="192.168.1.0/25")                # .0 to .127
        report = build_change_report(scan(1, MONDAY, scope="192.168.1.0/24"), narrower, [
            seen(1, 10, "192.168.1.100"),        # inside /25: missing
            seen(1, 11, "192.168.1.200"),        # outside /25: wasn't looked for
            seen(1, 12, None),                   # no IP: can't say
        ])
        self.assertEqual(ids(report.missing), [10])
        self.assertEqual(report.outside_scope, 2)

    def test_an_unreadable_scope_marks_nothing_missing(self) -> None:
        report = build_change_report(FROM, scan(2, TUESDAY, scope="not a range"), [seen(1, 10, "192.168.1.10")])
        self.assertEqual((report.missing, report.outside_scope), ((), 1))

    def test_first_seen_or_back_again(self) -> None:
        report = build_change_report(FROM, TO, [
            seen(2, 20, "192.168.1.20", first_seen=TUESDAY),                    # the moment the scan began
            seen(2, 21, "192.168.1.21", first_seen=datetime(2026, 11, 17, 9, 1)),   # during the scan
            seen(2, 22, "192.168.1.22", first_seen=datetime(2026, 11, 1)),      # known, absent on Monday
        ])
        self.assertEqual([device.first_seen_in_this_scan for device in report.new], [True, True, False])

    def test_randomized_macs_are_counted_per_kind(self) -> None:
        report = build_change_report(FROM, TO, [
            seen(2, 20, "192.168.1.20", randomized=True), seen(2, 21, "192.168.1.21", randomized=True),
            seen(2, 22, "192.168.1.22"),
            seen(1, 10, "192.168.1.10", randomized=True),
        ])
        self.assertEqual(report.randomized_counts,
                         {ChangeKind.NEW: 2, ChangeKind.MISSING: 1, ChangeKind.CHANGED: 0})
        self.assertTrue(report.new[0].is_randomized_mac)

    def test_a_partial_scan_on_either_side_warns(self) -> None:
        partial = ScanOutcome.PARTIAL
        cases = [(FROM, TO, False), (scan(1, MONDAY, outcome=partial), TO, True),
                 (FROM, scan(2, TUESDAY, outcome=partial), True)]
        for earlier, later, warned in cases:
            with self.subTest(earlier=earlier.outcome, later=later.outcome):
                self.assertEqual(build_change_report(earlier, later, []).partial_warning, warned)

    def test_sorted_by_ip_as_numbers(self) -> None:
        report = build_change_report(FROM, TO, [
            seen(2, 1, "192.168.1.10"), seen(2, 2, "192.168.1.9"), seen(2, 3, None),
            seen(2, 4, "192.168.1.100"), seen(2, 5, "10.0.0.1"),
        ])
        self.assertEqual([device.new_ip for device in report.new],
                         ["10.0.0.1", "192.168.1.9", "192.168.1.10", "192.168.1.100", None])

    def test_missing_sorts_by_its_old_ip(self) -> None:
        report = build_change_report(FROM, TO, [seen(1, 1, "192.168.1.10"), seen(1, 2, "192.168.1.9")])
        self.assertEqual([device.old_ip for device in report.missing], ["192.168.1.9", "192.168.1.10"])

    def test_rows_of_other_scans_are_ignored(self) -> None:
        report = build_change_report(FROM, TO, [seen(3, 30, "192.168.1.30")])
        self.assertEqual((report.new, report.missing), ((), ()))

#the service, with a fake store
class FakeScans:
    def __init__(self, scans: list[Scan], rows: list[ComparedObservation] = ()) -> None:
        self.scans = {item.pk: item for item in scans}
        self.rows = list(rows)
        self.units: list[Any] = []
        self.compared: list[tuple[int, int]] = []

    def find(self, scan_id: int, unit: Any = None) -> Scan | None:
        self.units.append(unit)
        return self.scans.get(scan_id)

    def finished(self, page: int = 1, per_page: int = 20, unit: Any = None) -> ScanPage:
        self.units.append(unit)
        done = sorted((item for item in self.scans.values() if is_comparable(item)),
                      key=lambda item: (item.started_at, item.pk), reverse=True)
        start = (page - 1) * per_page
        return ScanPage([FinishedScan(item, "admin", 0) for item in done[start:start + per_page]],
                        page, per_page, len(done))

    def compare_rows(self, from_id: int, to_id: int, unit: Any = None) -> list[ComparedObservation]:
        self.units.append(unit)
        self.compared.append((from_id, to_id))
        return [row for row in self.rows if row.scan_id in (from_id, to_id)]

class ChangeReportServiceTests(unittest.TestCase):

    def service(self, scans: list[Scan], rows: list[ComparedObservation] = ()) -> tuple[ChangeReportService, FakeScans]:
        fake = FakeScans(scans, rows)
        self.opened = 0

        @contextmanager
        def unit_of_work():
            self.opened += 1
            yield f"unit {self.opened}"

        return ChangeReportService(fake, unit_of_work), fake

    def test_no_ids_compares_the_latest_two_finished_scans(self) -> None:
        scans = [scan(1, datetime(2026, 11, 14)), scan(2, MONDAY),
                 scan(3, datetime(2026, 11, 16, 12), outcome=ScanOutcome.FAILED),          # newer, but failed
                 scan(4, TUESDAY),
                 scan(5, datetime(2026, 11, 17, 10), outcome=None, status=ScanStatus.RUNNING)]
        service, fake = self.service(scans, [seen(4, 40, "192.168.1.40")])
        result = service.report()
        self.assertEqual(result.status, MaintenanceStatus.DONE, result.reasons)
        self.assertEqual((result.record.from_scan.scan_id, result.record.to_scan.scan_id), (2, 4))
        self.assertEqual(ids(result.record.new), [40])
        self.assertEqual(fake.compared, [(2, 4)])

    def test_everything_happens_in_one_unit_of_work(self) -> None:
        service, fake = self.service([scan(1, MONDAY), scan(2, TUESDAY)])
        service.report(1, 2)
        self.assertEqual(self.opened, 1)
        self.assertEqual(set(fake.units), {"unit 1"})

    def test_two_ids_in_order(self) -> None:
        service, fake = self.service([scan(1, MONDAY), scan(2, TUESDAY, outcome=ScanOutcome.PARTIAL)])
        result = service.report(from_id=1, to_id=2)
        self.assertEqual(result.status, MaintenanceStatus.DONE, result.reasons)
        self.assertTrue(result.record.partial_warning)

    def test_refusals_and_their_reasons(self) -> None:
        scans = [scan(1, MONDAY), scan(2, TUESDAY), scan(3, TUESDAY, outcome=ScanOutcome.FAILED),
                 scan(4, TUESDAY, outcome=None, status=ScanStatus.RUNNING)]
        cases = [
            (dict(from_id=1), MaintenanceStatus.REJECTED, "Choose both scans"),
            (dict(to_id=2), MaintenanceStatus.REJECTED, "Choose both scans"),
            (dict(from_id=2, to_id=2), MaintenanceStatus.REJECTED, "not scan 2 twice"),
            (dict(from_id=2, to_id=1), MaintenanceStatus.REJECTED, "Scan 2 started after scan 1"),
            (dict(from_id=1, to_id=99), MaintenanceStatus.NOT_FOUND, "Scan 99 doesn't exist or hasn't finished."),
            (dict(from_id=3, to_id=2), MaintenanceStatus.NOT_FOUND, "Scan 3 doesn't exist or hasn't finished."),
            (dict(from_id=1, to_id=4), MaintenanceStatus.NOT_FOUND, "Scan 4 doesn't exist or hasn't finished."),
        ]
        for ids_given, status, reason in cases:
            with self.subTest(**ids_given):
                service, fake = self.service(scans)
                result = service.report(**ids_given)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.record)
                self.assertIn(reason, result.reasons[0])
                self.assertEqual(fake.compared, [])                         

    def test_both_unknown_scans_are_named(self) -> None:
        service, _ = self.service([])
        result = service.report(from_id=98, to_id=99)
        self.assertEqual(result.reasons, ("Scan 98 doesn't exist or hasn't finished.",
                                          "Scan 99 doesn't exist or hasn't finished."))

    def test_fewer_than_two_finished_scans(self) -> None:
        for scans, reason in [([], "there are 0"), ([scan(1, MONDAY)], "there is 1"),
                              ([scan(1, MONDAY), scan(2, TUESDAY, outcome=ScanOutcome.FAILED)], "there is 1")]:
            with self.subTest(count=len(scans)):
                service, _ = self.service(scans)
                result = service.report()
                self.assertEqual(result.status, MaintenanceStatus.REJECTED)
                self.assertIn(reason, result.reasons[0])

if __name__ == "__main__":
    unittest.main()