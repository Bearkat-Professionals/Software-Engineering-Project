"""Tests for 3.1 Network Data (#15).

Runs against the TEST database (same one-time setup as test_repository.py)
and is skipped without it.

    python -m unittest discover tests
"""
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from typing import Any

from backend.data_access.network_data import (DeviceSearch, DeviceStore, EditConflictError,
                                              ScanNotRunningError, ScanStore)
from backend.data_access.network_data.device_store import _search_where
from backend.data_access.network_data.scan_store import COMPARE_SQL
from backend.data_access.reference_data import ReferenceStore
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import AuditLog, Device, DeviceType, IdentificationEvidence, Scan, ScanObservation, User, Vendor
from backend.records.enums import EvidenceType, NetworkStatus, ScanOutcome, ScanStatus
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

# CLEANUP keeps device types and signatures; the device list test adds a type, so clear them too
EXTRA_CLEANUP = ("DELETE FROM device_signature", "DELETE FROM device_type WHERE device_type_id <> 1")


class NetworkDataTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP + EXTRA_CLEANUP:
                cursor.execute(sql)
        users = Repository(self.engine, User)
        self.admin = users.create(User(username="admin", password_hash="not-a-real-hash")).pk
        self.other = users.create(User(username="other", password_hash="not-a-real-hash")).pk
        self.devices = DeviceStore(self.engine)
        self.scans = ScanStore(self.engine)

    def device(self, mac, **fields):
        return self.devices.store(Device(mac_address=mac, **fields), self.admin)

    def scan(self, started_at=None, seen=()):
        scan = self.scans.start(Scan(user_id=self.admin, target_scope="192.168.1.0/24", started_at=started_at))
        for device_id in seen:
            self.scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=device_id))
        return scan

    def evidence(self, device_id, evidence_type=EvidenceType.OUI, raw_value="F0:D1:A9", weight=20):
        return self.scans.add_evidence(IdentificationEvidence(
            device_id=device_id, evidence_type=evidence_type, raw_value=raw_value, weight=weight))

    def rows(self, entity_type, **filters):
        return Repository(self.engine, entity_type).list(**filters)


class DoneWhenTests(NetworkDataTestCase):
    """The ticket's three "Done when" checks."""

    def test_saving_the_same_mac_twice_updates_one_row(self):
        first = self.device("F0:D1:A9:00:00:01", current_ip="192.168.1.5")
        second = self.device("f0:d1:a9:00:00:01", current_ip="192.168.1.9")    # new IP, lowercase
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(len(self.rows(Device)), 1)
        self.assertEqual(second.current_ip, "192.168.1.9")

    def test_devices_with_randomized_macs_are_not_merged(self):
        fixed = self.device("DA:00:00:00:00:01")
        randomized = self.device("DA:00:00:00:00:01", is_randomized_mac=True)    # same text, randomized
        another = self.device("DA:00:00:00:00:02", is_randomized_mac=True)
        again = self.device("DA:00:00:00:00:01", is_randomized_mac=True)          # same randomized MAC again
        self.assertEqual(len({fixed.pk, randomized.pk, another.pk}), 3)
        self.assertEqual(again.pk, randomized.pk)
        self.assertEqual(len(self.rows(Device)), 3)

    def test_deleting_a_device_leaves_no_observations_or_evidence(self):
        kept = self.device("F0:D1:A9:00:00:01")
        gone = self.device("F0:D1:A9:00:00:02")
        for day in (1, 2):
            self.scan(datetime(2026, 10, day), seen=(kept.pk, gone.pk))
        self.evidence(kept.pk)
        self.evidence(gone.pk)

        removed = self.devices.delete(gone.pk, self.admin)
        self.assertEqual(removed, {"observations_removed": 2, "evidence_removed": 1})
        self.assertIsNone(self.devices.find(gone.pk))
        self.assertEqual(self.rows(ScanObservation, device_id=gone.pk), [])
        self.assertEqual(self.rows(IdentificationEvidence, device_id=gone.pk), [])
        self.assertEqual(len(self.rows(ScanObservation, device_id=kept.pk)), 2)    # other device untouched
        self.assertEqual(len(self.rows(Scan)), 2)                                  # scan history kept


class DeviceStoreTests(NetworkDataTestCase):
    def test_stored_device_comes_back_with_database_defaults(self):
        device = self.device("F0:D1:A9:00:00:01")
        self.assertEqual((device.vendor_id, device.device_type_id), (1, 1))

    def test_every_change_is_audited(self):
        device = self.device("F0:D1:A9:00:00:01", current_ip="192.168.1.5")
        self.device("F0:D1:A9:00:00:01", current_ip="192.168.1.9")
        self.devices.delete(device.pk, self.admin)
        entries = self.rows(AuditLog, record_type="device")
        self.assertEqual([entry.action.value for entry in entries], ["insert", "update", "delete"])
        self.assertEqual(entries[1].old_value["current_ip"], "192.168.1.5")
        self.assertEqual(entries[2].old_value["mac_address"], "F0:D1:A9:00:00:01")

    def test_missing_device_is_reported_and_nothing_saved(self):
        with self.assertRaises(RecordNotFoundError):
            self.devices.store(Device(device_id=999_999, mac_address="F0:D1:A9:00:00:01"), self.admin)
        with self.assertRaises(RecordNotFoundError):
            self.devices.delete(999_999, self.admin)
        self.assertEqual(self.rows(AuditLog), [])

    # ---- edit conflicts --------------------------------------------------------------

    def test_save_is_refused_when_someone_else_changed_the_device(self):
        device = self.device("F0:D1:A9:00:00:01")
        loaded_at = datetime.now()
        time.sleep(0.01)
        theirs = self.devices.find(device.pk)
        theirs.device_name = "Living room TV"
        self.devices.store(theirs, self.other)

        mine = self.devices.find(device.pk)
        mine.device_name = "Kitchen TV"
        with self.assertRaises(EditConflictError) as caught:
            self.devices.store(mine, self.admin, loaded_at=loaded_at)
        self.assertEqual(caught.exception.changed_by, self.other)
        self.assertEqual(self.devices.find(device.pk).device_name, "Living room TV")
        with self.assertRaises(EditConflictError):
            self.devices.delete(device.pk, self.admin, loaded_at=loaded_at)

    def test_save_is_accepted_when_nothing_changed_or_no_loaded_at(self):
        device = self.device("F0:D1:A9:00:00:01")
        time.sleep(0.01)
        device.device_name = "Kitchen TV"
        self.devices.store(device, self.admin, loaded_at=datetime.now())
        device.device_name = "Office TV"
        self.devices.store(device, self.admin)                                    # scans pass no loaded_at
        self.assertEqual(self.devices.find(device.pk).device_name, "Office TV")

    def test_two_saves_at_once_take_turns(self):
        device = self.device("F0:D1:A9:00:00:01")
        loaded_at = datetime.now()
        time.sleep(0.01)
        first_saved = threading.Event()
        order = []

        def slow_save():
            with self.engine.unit_of_work() as cursor:
                theirs = self.devices.find(device.pk, cursor)
                theirs.device_name = "first"
                self.devices.store(theirs, self.other, cursor)
                first_saved.set()
                time.sleep(0.5)                                                  # hold the row lock
                order.append("first committed")

        worker = threading.Thread(target=slow_save)
        worker.start()
        first_saved.wait()
        mine = self.devices.find(device.pk)
        mine.device_name = "second"
        try:
            self.devices.store(mine, self.admin, loaded_at=loaded_at)
            order.append("second saved")
        except EditConflictError:
            order.append("second refused")
        worker.join()
        self.assertEqual(order, ["first committed", "second refused"])

    # ---- the device list ---------------------------------------------------------------

    def test_device_list_has_names_newest_first_and_pages(self):
        apple = ReferenceStore(self.engine, Vendor).store(Vendor(vendor_name="Apple, Inc."), self.admin)
        phone = ReferenceStore(self.engine, DeviceType).store(DeviceType(category_name="phone"), self.admin)
        seen = datetime(2026, 10, 26, 12, 0)
        for i in range(5):
            self.device(f"F0:D1:A9:00:00:0{i}", last_seen=seen + timedelta(minutes=i),
                        vendor_id=apple.pk if i % 2 else None,
                        device_type_id=phone.pk if i == 4 else None)
        self.device("F0:D1:A9:00:00:09")                                          # never seen
        ReferenceStore(self.engine, Vendor).retire(apple.pk, self.admin)

        first = self.devices.list_page(page=1, per_page=4)
        self.assertEqual(first.total, 6)
        self.assertEqual([row.device.mac_address[-2:] for row in first.devices], ["04", "03", "02", "01"])
        self.assertEqual(first.devices[0].device_type_name, "phone")
        self.assertEqual(first.devices[1].vendor_name, "Apple, Inc.")              # retired, still named
        self.assertEqual(first.devices[2].vendor_name, "unknown")
        second = self.devices.list_page(page=2, per_page=4)
        self.assertEqual([row.device.mac_address[-2:] for row in second.devices], ["00", "09"])
        self.assertEqual(self.devices.list_page(page=3, per_page=4).devices, [])

    def test_device_list_can_be_narrowed_by_criteria(self):
        self.device("F0:D1:A9:00:00:01", flagged_status=True, network_status=NetworkStatus.ONLINE)
        self.device("F0:D1:A9:00:00:02", network_status=NetworkStatus.ONLINE)
        self.device("F0:D1:A9:00:00:03")
        self.assertEqual(self.devices.list_page(flagged_status=True).total, 1)
        self.assertEqual(self.devices.list_page(network_status=NetworkStatus.ONLINE).total, 2)

    def test_device_list_rejects_bad_paging_and_unknown_criteria(self):
        for bad in (dict(page=0), dict(per_page=0), dict(per_page=201), dict(hostname="tv")):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    self.devices.list_page(**bad)


class FindListingTests(NetworkDataTestCase):
    """3.1.2 for the edit form: one device with its vendor and type NAMES."""

    def test_one_device_with_its_vendor_and_type_names(self):
        acme = ReferenceStore(self.engine, Vendor).store(Vendor(vendor_name="Acme"), self.admin)
        printer = ReferenceStore(self.engine, DeviceType).store(DeviceType(category_name="printer"), self.admin)
        device = self.device("28:6F:B9:00:00:01", vendor_id=acme.pk, device_type_id=printer.pk,
                             device_name="Office printer")
        listing = self.devices.find_listing(device.pk)
        self.assertEqual((listing.device.pk, listing.device.device_name, listing.vendor_name, listing.device_type_name),
                         (device.pk, "Office printer", "Acme", "printer"))

    def test_it_is_the_same_as_the_devices_row_in_the_list(self):
        device = self.device("28:6F:B9:00:00:02", device_name="Den TV")
        [row] = self.devices.list_page().devices
        self.assertEqual(self.devices.find_listing(device.pk), row)

    def test_a_new_device_shows_the_unknown_names(self):
        device = self.device("28:6F:B9:00:00:03")
        listing = self.devices.find_listing(device.pk)
        self.assertEqual((listing.vendor_name.casefold(), listing.device_type_name.casefold()), ("unknown", "unknown"))

    def test_a_retired_vendor_still_shows_its_name(self):                     # DI-06
        vendors = ReferenceStore(self.engine, Vendor)
        old = vendors.store(Vendor(vendor_name="OldCo"), self.admin)
        device = self.device("28:6F:B9:00:00:04", vendor_id=old.pk)
        vendors.retire(old.pk, self.admin)
        self.assertEqual(self.devices.find_listing(device.pk).vendor_name, "OldCo")

    def test_a_missing_device_is_none(self):
        self.assertIsNone(self.devices.find_listing(999_999))


class DeviceSearchTests(NetworkDataTestCase):
    """3.1.2 search: FR-10 filters, text search, sorting and paging."""

    def add(self, n: int, **fields: Any) -> Device:
        return self.device(f"F0:D1:A9:00:00:{n:02d}", **fields)

    def found(self, page: int = 1, per_page: int = 50, **fields: Any) -> list[str]:
        """The last two characters of each MAC found, in the order returned."""
        result = self.devices.search(DeviceSearch(**fields), page, per_page)
        return [row.device.mac_address[-2:] for row in result.devices]

    def check(self, cases: list[tuple[dict[str, Any], list[str]]]) -> None:
        """Each search finds exactly these devices (in any order)."""
        for fields, expected in cases:
            with self.subTest(**fields):
                self.assertEqual(sorted(self.found(**fields)), expected)

    # ---- filters -----------------------------------------------------------------------

    def test_each_filter_alone_and_three_together(self) -> None:
        phone = ReferenceStore(self.engine, DeviceType).store(DeviceType(category_name="phone"), self.admin)
        apple = ReferenceStore(self.engine, Vendor).store(Vendor(vendor_name="Apple, Inc."), self.admin)
        online, offline = NetworkStatus.ONLINE, NetworkStatus.OFFLINE
        self.add(1, device_type_id=phone.pk, vendor_id=apple.pk, network_status=online,
                 flagged_status=True, confidence_score=80)
        self.add(2, device_type_id=phone.pk, network_status=online, flagged_status=True, confidence_score=30)
        self.add(3, vendor_id=apple.pk, network_status=offline, trusted_status=True, confidence_score=90)
        self.add(4, is_randomized_mac=True, network_status=online, confidence_score=60)
        self.check([
            (dict(device_type_id=phone.pk), ["01", "02"]),
            (dict(vendor_id=apple.pk), ["01", "03"]),
            (dict(network_status=offline), ["03"]),
            (dict(flagged_status=True), ["01", "02"]),
            (dict(trusted_status=True), ["03"]),
            (dict(trusted_status=False), ["01", "02", "04"]),
            (dict(is_randomized_mac=True), ["04"]),
            (dict(confidence_min=60), ["01", "03", "04"]),
            (dict(network_status=online, flagged_status=True, confidence_min=50), ["01"]),
        ])

    def test_confidence_range_edges(self) -> None:
        for n, score in enumerate((0, 1, 50, 99, 100), start=1):
            self.add(n, confidence_score=score)
        self.check([
            (dict(confidence_min=0, confidence_max=100), ["01", "02", "03", "04", "05"]),
            (dict(confidence_max=0), ["01"]),
            (dict(confidence_min=100), ["05"]),
            (dict(confidence_min=50, confidence_max=50), ["03"]),
            (dict(confidence_min=1, confidence_max=99), ["02", "03", "04"]),
        ])

    def test_date_range_includes_all_of_the_last_day(self) -> None:
        self.add(1, last_seen=datetime(2026, 11, 7, 23, 59, 59))     # the day before
        self.add(2, last_seen=datetime(2026, 11, 8, 0, 0, 0))        # first second of seen_from
        self.add(3, last_seen=datetime(2026, 11, 9, 23, 59, 59))     # last second of seen_to
        self.add(4, last_seen=datetime(2026, 11, 10, 0, 0, 0))       # the day after
        self.add(5)                                                  # never seen: outside any range
        self.check([
            (dict(seen_from=date(2026, 11, 8), seen_to=date(2026, 11, 9)), ["02", "03"]),
            (dict(seen_from=date(2026, 11, 9), seen_to=date(2026, 11, 9)), ["03"]),
            (dict(seen_from=date(2026, 11, 9)), ["03", "04"]),
            (dict(seen_to=date(2026, 11, 7)), ["01"]),
        ])

    # ---- text search -------------------------------------------------------------------

    def test_text_matches_each_column_and_ignores_case(self) -> None:
        self.add(1, device_name="Kitchen TV")
        self.add(2, hostname="living-room-printer.local")
        self.add(3, current_ip="172.16.44.7")
        self.device("AA:BB:CC:00:00:04")
        self.check([
            (dict(text="kITCHEN"), ["01"]),            # name
            (dict(text="LIVING-ROOM"), ["02"]),        # hostname
            (dict(text="16.44"), ["03"]),              # IP
            (dict(text="aa:bb:cc"), ["04"]),           # MAC
            (dict(text="  kitchen  "), ["01"]),        # spaces around it are ignored
            (dict(text="x' OR '1'='1"), []),           # just text, not SQL
        ])

    def test_wildcards_in_the_text_match_only_themselves(self) -> None:
        self.add(1, device_name="50% off sign")
        self.add(2, device_name="500 club")              # would match an unescaped 50%
        self.add(3, device_name="cam a_b")
        self.add(4, device_name="cam axb")               # would match an unescaped a_b
        self.add(5, device_name="back\\slash")
        self.check([
            (dict(text="50%"), ["01"]),
            (dict(text="a_b"), ["03"]),
            (dict(text="k\\s"), ["05"]),
        ])

    # ---- sorting and paging ------------------------------------------------------------

    def test_ip_sorts_as_numbers_and_no_ip_comes_last(self) -> None:
        for n, ip in enumerate(("10.0.0.10", "9.0.0.1", "10.0.0.100", None, "10.0.0.9"), start=1):
            self.add(n, current_ip=ip)
        self.assertEqual(self.found(sort="ip", descending=False), ["02", "05", "01", "03", "04"])
        self.assertEqual(self.found(sort="ip", descending=True), ["03", "01", "05", "02", "04"])

    def test_devices_without_a_name_sort_last_either_way(self) -> None:
        for n, name in enumerate(("bravo", None, "Alpha", "charlie"), start=1):
            self.add(n, device_name=name)
        self.assertEqual(self.found(sort="name", descending=False), ["03", "01", "04", "02"])
        self.assertEqual(self.found(sort="name", descending=True), ["04", "01", "03", "02"])

    def test_every_sort_orders_by_its_column(self) -> None:
        apple = ReferenceStore(self.engine, Vendor).store(Vendor(vendor_name="Apple, Inc."), self.admin)
        zebra = ReferenceStore(self.engine, Vendor).store(Vendor(vendor_name="Zebra Technologies"), self.admin)
        phone = ReferenceStore(self.engine, DeviceType).store(DeviceType(category_name="phone"), self.admin)
        self.add(1, vendor_id=zebra.pk, device_type_id=phone.pk, confidence_score=50,
                 last_seen=datetime(2026, 11, 9))
        self.add(2, vendor_id=apple.pk, confidence_score=90, last_seen=datetime(2026, 11, 7))
        self.add(3, confidence_score=10, last_seen=datetime(2026, 11, 8))     # unknown vendor and type
        ascending = {
            "confidence": ["03", "01", "02"],
            "last_seen": ["02", "03", "01"],
            "vendor": ["02", "03", "01"],          # Apple, unknown, Zebra
            "type": ["01", "02", "03"],            # phone, then two "unknown": the id breaks the tie
        }
        for sort, expected in ascending.items():
            with self.subTest(sort=sort):
                self.assertEqual(self.found(sort=sort, descending=False), expected)
                # descending is the exact reverse, ties included, because the id follows the direction
                self.assertEqual(self.found(sort=sort, descending=True), expected[::-1])

    def test_paging_a_sorted_search_returns_every_device_once(self) -> None:
        for n in range(1, 8):
            self.add(n, confidence_score=50)            # all tied, so only device_id decides the order
        for descending in (False, True):
            with self.subTest(descending=descending):
                pages = [self.found(page, 3, sort="confidence", descending=descending) for page in (1, 2, 3)]
                self.assertEqual([len(found) for found in pages], [3, 3, 1])
                every = [mac for found in pages for mac in found]
                self.assertEqual(sorted(every), [f"{n:02d}" for n in range(1, 8)])

    def test_total_counts_every_match_not_just_the_page(self) -> None:
        for n in range(1, 8):
            self.add(n, flagged_status=n <= 5, device_name="cam" if n % 2 else "tv")
        flagged_cams = self.devices.search(DeviceSearch(flagged_status=True, text="cam"), page=1, per_page=2)
        self.assertEqual((flagged_cams.total, len(flagged_cams.devices)), (3, 2))     # 1, 3 and 5
        last_page = self.devices.search(DeviceSearch(flagged_status=True), page=3, per_page=2)
        self.assertEqual((last_page.total, len(last_page.devices)), (5, 1))


class DeviceSearchRuleTests(unittest.TestCase):
    """DeviceSearch checks itself, and no search value ever becomes SQL text. No database."""

    def test_bad_searches_are_refused(self) -> None:
        bad = [dict(confidence_min=-1), dict(confidence_max=101),
               dict(confidence_min=60, confidence_max=50),
               dict(seen_from=date(2026, 11, 9), seen_to=date(2026, 11, 8)),
               dict(sort="mac_address"), dict(sort="last_seen; DROP TABLE device")]
        for fields in bad:
            with self.subTest(**fields):
                with self.assertRaises(ValueError):
                    DeviceSearch(**fields)

    def test_blank_text_and_datetimes_are_tidied(self) -> None:
        self.assertIsNone(DeviceSearch(text="   ").text)
        self.assertEqual(DeviceSearch(text=" tv ").text, "tv")
        self.assertEqual(DeviceSearch(seen_to=datetime(2026, 11, 9, 15, 30)).seen_to, date(2026, 11, 9))

    def test_values_are_parameters_never_sql(self) -> None:
        sneaky = "x' OR '1'='1"
        search = DeviceSearch(device_type_id=2, vendor_id=3, network_status=NetworkStatus.ONLINE,
                              flagged_status=True, trusted_status=False, is_randomized_mac=False,
                              confidence_min=10, confidence_max=90, seen_from=date(2026, 11, 1),
                              seen_to=date(2026, 11, 9), text=sneaky)
        sql, params = _search_where(search)
        self.assertNotIn(sneaky, sql)
        self.assertNotIn("'1'", sql)
        self.assertEqual(sql.count("%s"), len(params))         # one parameter for every placeholder
        self.assertIn(f"%{sneaky}%", params)
        self.assertIn("online", params)
        self.assertIn(datetime(2026, 11, 10), params)          # seen_to + 1 day, at midnight


class ScanStoreTests(NetworkDataTestCase):
    def test_scan_starts_running_and_finishes_once(self):
        scan = self.scans.start(Scan(user_id=self.admin, target_scope="192.168.1.0/24",
                                     configuration_used={"timeout": 1}))
        self.assertEqual((scan.status, scan.outcome), (ScanStatus.RUNNING, None))
        self.assertEqual(scan.configuration_used, {"timeout": 1})
        self.assertEqual(self.scans.find_running().pk, scan.pk)

        done = self.scans.finish(scan.pk, ScanOutcome.PARTIAL, "mDNS unavailable")
        self.assertEqual((done.status, done.outcome), (ScanStatus.COMPLETED, ScanOutcome.PARTIAL))
        self.assertEqual(done.error_message, "mDNS unavailable")
        self.assertIsNone(self.scans.find_running())
        with self.assertRaises(ScanNotRunningError):
            self.scans.finish(scan.pk, ScanOutcome.SUCCESS)
        with self.assertRaises(RecordNotFoundError):
            self.scans.finish(999_999, ScanOutcome.SUCCESS)

    def test_a_scan_records_each_device_once(self):
        device = self.device("F0:D1:A9:00:00:01")
        scan = self.scan()
        self.scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=device.pk, ip_address="192.168.1.5"))
        self.scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=device.pk, ip_address="192.168.1.9"))
        observations = self.rows(ScanObservation)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].ip_address, "192.168.1.9")

    def test_evidence_is_not_duplicated_on_rescans(self):
        device = self.device("F0:D1:A9:00:00:01")
        self.assertTrue(self.evidence(device.pk)[1])
        self.assertFalse(self.evidence(device.pk)[1])                              # same evidence again
        updated, added = self.evidence(device.pk, weight=30)                       # the rule's weight changed
        self.assertFalse(added)
        self.assertEqual(updated.weight, 30)
        self.assertTrue(self.evidence(device.pk, evidence_type=EvidenceType.MDNS)[1])
        self.assertEqual(len(self.rows(IdentificationEvidence)), 2)

    def test_observation_and_evidence_roll_back_together(self):
        device = self.device("F0:D1:A9:00:00:01")
        scan = self.scan()
        with self.assertRaises(RuntimeError):
            with self.engine.unit_of_work() as cursor:
                self.scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=device.pk), cursor)
                self.scans.add_evidence(IdentificationEvidence(
                    device_id=device.pk, evidence_type=EvidenceType.OUI, raw_value="F0:D1:A9"), cursor)
                raise RuntimeError("something failed later in the same save")
        self.assertEqual(self.rows(ScanObservation), [])
        self.assertEqual(self.rows(IdentificationEvidence), [])

    # ---- 3.1.5 scan history ------------------------------------------------------------

    def test_history_returns_a_range_newest_first_with_observations(self):
        tv = self.device("F0:D1:A9:00:00:01").pk
        phone = self.device("F0:D1:A9:00:00:02").pk
        early = self.scan(datetime(2026, 10, 1, 9), seen=(tv, phone))
        middle = self.scan(datetime(2026, 10, 15, 9), seen=(tv,))
        late = self.scan(datetime(2026, 10, 31, 23, 59, 59))
        november = self.scan(datetime(2026, 11, 1), seen=(phone,))

        october = self.scans.history(datetime(2026, 10, 1), datetime(2026, 11, 1))
        self.assertEqual([entry.scan.pk for entry in october], [late.pk, middle.pk, early.pk])
        self.assertEqual([len(entry.observations) for entry in october], [0, 1, 2])
        self.assertEqual(self.scans.latest().pk, november.pk)
        with self.assertRaises(ValueError):
            self.scans.history(datetime(2026, 11, 1), datetime(2026, 10, 1))

    def test_nothing_to_report_before_the_first_scan(self):
        self.assertIsNone(self.scans.latest())
        self.assertEqual(self.scans.history(datetime(2026, 1, 1), datetime(2027, 1, 1)), [])

    def test_device_history_shows_where_a_device_was_seen(self):
        tv = self.device("F0:D1:A9:00:00:01").pk
        phone = self.device("F0:D1:A9:00:00:02").pk
        first = self.scan(datetime(2026, 10, 1), seen=(tv, phone))
        second = self.scan(datetime(2026, 10, 2), seen=(tv,))
        third = self.scan(datetime(2026, 10, 3), seen=(phone,))
        self.assertEqual([o.scan_id for o in self.scans.device_history(tv)], [second.pk, first.pk])
        self.assertEqual([o.scan_id for o in self.scans.device_history(phone, start=datetime(2026, 10, 2))],
                         [third.pk])
        self.evidence(tv)
        self.assertEqual(len(self.scans.evidence_for(tv)), 1)
        self.assertEqual(self.scans.evidence_for(phone), [])

class ChangeReportReadTests(NetworkDataTestCase):
    """2.4.3: the two reads behind the Change Report."""

    def finished_scan(self, started_at, outcome=ScanOutcome.SUCCESS, user_id=None, seen=()):
        scan = self.scans.start(Scan(user_id=user_id or self.admin, target_scope="192.168.1.0/24",
                                     started_at=started_at))
        for device_id, ip, hostname in seen:
            self.scans.add_observation(ScanObservation(scan_id=scan.pk, device_id=device_id,
                                                       ip_address=ip, hostname=hostname))
        return self.scans.finish(scan.pk, outcome) if outcome else scan

    def test_finished_lists_only_comparable_scans_newest_first(self):
        tv = self.device("F0:D1:A9:00:00:01").pk
        phone = self.device("F0:D1:A9:00:00:02").pk
        first = self.finished_scan(datetime(2026, 11, 1), seen=((tv, "192.168.1.5", None), (phone, "192.168.1.6", None)))
        second = self.finished_scan(datetime(2026, 11, 2), ScanOutcome.PARTIAL, user_id=self.other,
                                    seen=((tv, "192.168.1.5", None),))
        self.finished_scan(datetime(2026, 11, 3), ScanOutcome.FAILED)            # failed: left out
        self.finished_scan(datetime(2026, 11, 4), outcome=None)                  # still running: left out

        page = self.scans.finished()
        self.assertEqual(page.total, 2)
        self.assertEqual([row.scan.pk for row in page.scans], [second.pk, first.pk])
        self.assertEqual([row.username for row in page.scans], ["other", "admin"])
        self.assertEqual([row.observation_count for row in page.scans], [1, 2])
        self.assertEqual(page.scans[0].scan.outcome, ScanOutcome.PARTIAL)
        self.assertNotIn("not-a-real-hash", repr(page))                         # no other user columns

        self.assertEqual([row.scan.pk for row in self.scans.finished(page=2, per_page=1).scans], [first.pk])
        for bad in (dict(page=0), dict(per_page=0), dict(per_page=201)):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    self.scans.finished(**bad)

    def test_compare_rows_brings_both_scans_with_the_device_names(self):
        acme = ReferenceStore(self.engine, Vendor).store(Vendor(vendor_name="Acme"), self.admin)
        printer = ReferenceStore(self.engine, DeviceType).store(DeviceType(category_name="printer"), self.admin)
        office = self.device("28:6F:B9:00:00:01", device_name="Office printer", vendor_id=acme.pk,
                             device_type_id=printer.pk, first_seen=datetime(2026, 10, 1, 8)).pk
        phone = self.device("DA:00:00:00:00:02", is_randomized_mac=True).pk
        other = self.device("F0:D1:A9:00:00:03").pk
        monday = self.finished_scan(datetime(2026, 11, 16), seen=((office, "192.168.1.5", "printer"),))
        tuesday = self.finished_scan(datetime(2026, 11, 17), seen=((office, "192.168.1.9", "printer"),
                                                                    (phone, "192.168.1.20", None)))
        self.finished_scan(datetime(2026, 11, 18), seen=((other, "192.168.1.30", None),))   # not asked for

        rows = self.scans.compare_rows(monday.pk, tuesday.pk)
        self.assertEqual(sorted((row.scan_id, row.device_id, row.ip_address, row.hostname) for row in rows),
                         sorted([(monday.pk, office, "192.168.1.5", "printer"),
                                 (tuesday.pk, office, "192.168.1.9", "printer"),
                                 (tuesday.pk, phone, "192.168.1.20", None)]))
        named = next(row for row in rows if row.device_id == office)
        self.assertEqual((named.device_name, named.vendor_name, named.device_type_name, named.mac_address),
                         ("Office printer", "Acme", "printer", "28:6F:B9:00:00:01"))
        self.assertEqual(named.first_seen, datetime(2026, 10, 1, 8))
        randomized = next(row for row in rows if row.device_id == phone)
        self.assertIs(randomized.is_randomized_mac, True)
        self.assertEqual(randomized.vendor_name.casefold(), "unknown")

    def test_compare_rows_can_use_the_indexes(self):
        """Data rule: scan_observation by scan_id or device_id, device by its key, never a full scan only."""
        with self.engine.unit_of_work() as cursor:
            cursor.execute("EXPLAIN " + COMPARE_SQL, (1, 2))
            plan = {row["table"]: row for row in cursor.fetchall()}
        for table in ("o", "d"):
            with self.subTest(table=table):
                self.assertIsNotNone(plan[table]["possible_keys"], plan[table])

if __name__ == "__main__":
    unittest.main()
