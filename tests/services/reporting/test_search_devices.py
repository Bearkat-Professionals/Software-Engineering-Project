"""Tests for 2.4.1 Search Devices with a fake store: no database.

    py -m unittest tests.services.reporting.test_search_devices -v
"""
from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from backend.data_access.network_data import DevicePage, DeviceSearch
from backend.data_access.network_data.device_store import DEFAULT_PER_PAGE, SORT_COLUMNS
from backend.records.enums import NetworkStatus
from backend.services.reporting.search_devices import TEXT_MAX, SearchResult, parse_search, search_devices

class FakeSearchStore:
    """DeviceStore.search in memory: records what it was asked, returns an empty page."""

    def __init__(self) -> None:
        self.calls: list[tuple[DeviceSearch, int, int]] = []

    def search(self, search: DeviceSearch = DeviceSearch(), page: int = 1,
               per_page: int = DEFAULT_PER_PAGE, unit: Any = None) -> DevicePage:
        self.calls.append((search, page, per_page))
        return DevicePage([], page, per_page, 0)

class ParseTestCase(unittest.TestCase):
    def accepted(self, params: dict[str, str]) -> DeviceSearch:
        search, reasons = parse_search(params)
        self.assertEqual(reasons, [], params)
        self.assertIsInstance(search, DeviceSearch)
        return search

    def refused(self, params: dict[str, str], *parts: str) -> list[str]:
        search, reasons = parse_search(params)
        self.assertIsNone(search, params)
        self.assertTrue(reasons)
        for part in parts:
            self.assertTrue(any(part in reason for reason in reasons), (part, reasons))
        return reasons

#each parameter, good and bad
class IdTests(ParseTestCase):
    def test_good(self) -> None:
        for name, field in (("type", "device_type_id"), ("vendor", "vendor_id")):
            for text, value in (("1", 1), ("42", 42), (" 7 ", 7), ("2147483647", 2147483647)):
                with self.subTest(name=name, text=text):
                    self.assertEqual(getattr(self.accepted({name: text}), field), value)

    def test_bad(self) -> None:
        for name in ("type", "vendor"):
            for text in ("0", "-1", "1.5", "abc", "1e3", "+3", "２", "2147483648", "9" * 30):
                with self.subTest(name=name, text=text):
                    self.refused({name: text}, f"{name} must be a whole number of 1 or more, not '")

class StatusTests(ParseTestCase):
    def test_good(self) -> None:
        for text, value in (("online", NetworkStatus.ONLINE), ("OFFLINE", NetworkStatus.OFFLINE)):
            with self.subTest(text=text):
                self.assertIs(self.accepted({"status": text}).network_status, value)

    def test_bad(self) -> None:
        self.refused({"status": "away"}, "status must be online or offline, not 'away'.")

class BooleanTests(ParseTestCase):
    def test_good(self) -> None:
        for name, field in (("flagged", "flagged_status"), ("trusted", "trusted_status")):
            for text, value in (("true", True), ("false", False), ("True", True)):
                with self.subTest(name=name, text=text):
                    self.assertIs(getattr(self.accepted({name: text}), field), value)

    def test_bad(self) -> None:
        for name in ("flagged", "trusted"):
            for text in ("yes", "1", "0", "t"):
                with self.subTest(name=name, text=text):
                    self.refused({name: text}, f"{name} must be true or false, not '{text}'.")

class ConfidenceTests(ParseTestCase):
    def test_good(self) -> None:
        search = self.accepted({"confidence_min": "0", "confidence_max": "100"})
        self.assertEqual((search.confidence_min, search.confidence_max), (0, 100))
        self.assertEqual(self.accepted({"confidence_min": "50", "confidence_max": "50"}).confidence_min, 50)

    def test_bad(self) -> None:
        for name in ("confidence_min", "confidence_max"):
            for text in ("-1", "101", "50.5", "high"):
                with self.subTest(name=name, text=text):
                    self.refused({name: text}, f"{name} must be a whole number from 0 to 100, not '{text}'.")

    def test_min_more_than_max(self) -> None:
        self.refused({"confidence_min": "80", "confidence_max": "20"},
                     "confidence_min (80) must not be more than confidence_max (20).")

class DateTests(ParseTestCase):
    def test_good(self) -> None:
        search = self.accepted({"seen_from": "2026-11-01", "seen_to": "2026-11-07"})
        self.assertEqual((search.seen_from, search.seen_to), (date(2026, 11, 1), date(2026, 11, 7)))
        self.assertEqual(self.accepted({"seen_from": "2026-11-07", "seen_to": "2026-11-07"}).seen_to,
                         date(2026, 11, 7))                           # one whole day

    def test_bad(self) -> None:
        for text in ("7th", "2026-02-30", "20261107", "11/07/2026", "2026-11-7", "2026-11-07T10:00"):
            with self.subTest(text=text):
                self.refused({"seen_to": text}, f"seen_to must be a date like 2026-11-07, not '{text}'.")

    def test_the_ticket_example(self) -> None:
        self.assertEqual(self.refused({"seen_to": "7th"}), ["seen_to must be a date like 2026-11-07, not '7th'."])

    def test_from_after_to(self) -> None:
        self.refused({"seen_from": "2026-11-08", "seen_to": "2026-11-07"},
                     "seen_from (2026-11-08) must not be after seen_to (2026-11-07).")

class TextTests(ParseTestCase):
    def test_trimmed(self) -> None:
        self.assertEqual(self.accepted({"q": "  roku  "}).text, "roku")

    def test_empty_means_no_text_search(self) -> None:
        for text in ("", "   "):
            with self.subTest(text=text):
                self.assertIsNone(self.accepted({"q": text}).text)

    def test_at_most_100(self) -> None:
        self.assertEqual(self.accepted({"q": "x" * TEXT_MAX}).text, "x" * TEXT_MAX)
        reasons = self.refused({"q": "x" * (TEXT_MAX + 1)}, "q must be at most 100 characters, not 101.")
        self.assertNotIn("xxxx", reasons[0])                          # the text isn't repeated back

    def test_wildcards_are_just_text(self) -> None:                   # the store escapes them
        self.assertEqual(self.accepted({"q": "50%_off"}).text, "50%_off")

class SortTests(ParseTestCase):
    def test_every_allowed_sort(self) -> None:
        for key in SORT_COLUMNS:
            with self.subTest(sort=key):
                self.assertEqual(self.accepted({"sort": key}).sort, key)

    def test_order(self) -> None:
        self.assertFalse(self.accepted({"order": "asc"}).descending)
        self.assertTrue(self.accepted({"order": "DESC"}).descending)

    def test_bad(self) -> None:
        self.refused({"sort": "mac_address"},
                     "sort must be last_seen, name, ip, confidence, vendor or type, not 'mac_address'.")
        self.refused({"order": "up"}, "order must be asc or desc, not 'up'.")

    def test_sql_in_sort_is_just_refused(self) -> None:
        self.refused({"sort": "name; DROP TABLE device"}, "sort must be")

#the rules across parameters
class TogetherTests(ParseTestCase):
    def test_every_problem_at_once(self) -> None:                     # OUT-04
        reasons = self.refused({"type": "x", "status": "away", "flagged": "maybe", "confidence_min": "500",
                                "seen_from": "yesterday", "sort": "size", "order": "up"})
        self.assertEqual(len(reasons), 7)
        for name in ("type", "status", "flagged", "confidence_min", "seen_from", "sort", "order"):
            self.assertTrue(any(reason.startswith(name + " ") for reason in reasons), name)

    def test_a_bad_side_skips_the_pair_check(self) -> None:
        reasons = self.refused({"confidence_min": "x", "confidence_max": "20"})
        self.assertEqual(len(reasons), 1)                             # only the bad value, no "more than"

    def test_blank_means_any(self) -> None:
        self.assertEqual(self.accepted({name: " " for name in ("type", "vendor", "status", "flagged", "trusted",
                                                               "confidence_min", "seen_to", "sort", "order")}),
                         DeviceSearch())

    def test_unknown_parameters_are_ignored(self) -> None:            # page and per_page share the query
        self.assertEqual(self.accepted({"page": "2", "per_page": "25", "foo": "bar"}), DeviceSearch())

    def test_everything_at_once(self) -> None:
        search = self.accepted({"type": "3", "vendor": "9", "status": "online", "flagged": "true",
                                "trusted": "false", "confidence_min": "20", "confidence_max": "90",
                                "seen_from": "2026-11-01", "seen_to": "2026-11-07", "q": "tv",
                                "sort": "ip", "order": "asc"})
        self.assertEqual(search, DeviceSearch(device_type_id=3, vendor_id=9, network_status=NetworkStatus.ONLINE,
                                              flagged_status=True, trusted_status=False, confidence_min=20,
                                              confidence_max=90, seen_from=date(2026, 11, 1),
                                              seen_to=date(2026, 11, 7), text="tv", sort="ip", descending=False))

#search_devices
class SearchDevicesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeSearchStore()

    def test_a_valid_search_reaches_the_store(self) -> None:
        result = search_devices(self.store, {"flagged": "true", "q": "roku"}, page=2, per_page=25)
        self.assertIsInstance(result, SearchResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.reasons, ())
        self.assertEqual(self.store.calls, [(DeviceSearch(flagged_status=True, text="roku"), 2, 25)])
        self.assertEqual((result.page.page, result.page.per_page), (2, 25))

    def test_nothing_reaches_the_store_when_any_value_is_bad(self) -> None:
        for params in ({"type": "x"}, {"q": "fine", "seen_to": "7th"}, {"confidence_min": "90", "confidence_max": "10"}):
            with self.subTest(params=params):
                result = search_devices(self.store, params)
                self.assertFalse(result.ok)
                self.assertIsNone(result.page)
                self.assertTrue(result.reasons)
        self.assertEqual(self.store.calls, [])

    def test_no_parameters_is_the_same_search_as_list_page(self) -> None:
        search_devices(self.store, {})
        # list_page() with no criteria calls search(DeviceSearch(), 1, DEFAULT_PER_PAGE)
        self.assertEqual(self.store.calls, [(DeviceSearch(), 1, DEFAULT_PER_PAGE)])

if __name__ == "__main__":
    unittest.main()