"""Tests for 3.1 Network Data (#15).

Runs against the TEST database (same one-time setup as test_repository.py)
and is skipped without it.

    python -m unittest discover tests
"""
import threading
import time
import unittest
from datetime import datetime, timedelta

from backend.data_access.network_data import DeviceStore, EditConflictError, ScanNotRunningError, ScanStore
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


if __name__ == "__main__":
    unittest.main()
