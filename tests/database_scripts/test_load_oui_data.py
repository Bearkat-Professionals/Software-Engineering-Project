"""Tests for the OUI seed script (#37).

The parser tests need no database. The load test uses the TEST database (same
one-time setup as tests/data_access/test_repository.py) and is skipped without it.

    python -m unittest discover tests
"""
import unittest
from pathlib import Path

from database.scripts.load_oui_data import load_oui_data, parse_oui_csv
from tests.data_access.test_repository import CLEANUP, connect_to_test_database

SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "oui_sample.csv"


class ParseOuiCsvTests(unittest.TestCase):
    def setUp(self):
        self.pairs, self.skipped = parse_oui_csv(SAMPLE)
        self.by_prefix = dict(self.pairs)

    def test_prefixes_are_uppercase_with_colons(self):
        self.assertEqual(self.by_prefix["28:6F:B9"], "Nokia Shanghai Bell Co., Ltd.")   # comma inside quotes
        self.assertEqual(self.by_prefix["F0:D1:A9"], "Apple, Inc.")                     # was lowercase f0d1a9

    def test_names_are_trimmed(self):
        self.assertEqual(self.by_prefix["70:B3:D5"], "Katun Corporation")

    def test_bad_rows_are_skipped_and_counted(self):
        # bad hex, 5 digits, empty name, a row of commas, a row missing columns
        self.assertEqual(self.skipped, 5)
        self.assertNotIn("70:13:01", self.by_prefix)

    def test_repeated_prefix_keeps_the_last_name(self):
        self.assertEqual(self.by_prefix["E8:0A:B9"], "Cisco Systems, Inc.")
        self.assertEqual(len(self.pairs), 6)


class LoadOuiDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = connect_to_test_database()

    def setUp(self):
        with self.engine.unit_of_work() as cursor:
            for sql in CLEANUP:
                cursor.execute(sql)

    def test_load_twice_changes_nothing_the_second_time(self):
        unknown_before = self.engine.fetch_one("SELECT * FROM vendor WHERE vendor_id = 1")
        pairs, _ = parse_oui_csv(SAMPLE)

        first = load_oui_data(self.engine, pairs)
        self.assertEqual(first["vendors_added"], 4)     # Apple's three rows (two spellings) are one vendor
        self.assertEqual(first["prefixes_added"], 6)
        self.assertEqual(first["prefixes_updated"], 0)

        second = load_oui_data(self.engine, pairs)
        self.assertEqual(second, {"vendors_added": 0, "prefixes_added": 0,
                                  "prefixes_updated": 0, "names_truncated": 0})

        apple = self.engine.fetch_all(
            "SELECT o.oui_prefix FROM oui_prefix o JOIN vendor v ON v.vendor_id = o.vendor_id "
            "WHERE v.vendor_name = 'Apple, Inc.' ORDER BY o.oui_prefix")
        self.assertEqual([row["oui_prefix"] for row in apple], ["3C:07:54", "A4:C3:61", "F0:D1:A9"])
        self.assertEqual(self.engine.fetch_one("SELECT COUNT(*) AS n FROM vendor")["n"], 5)   # 4 + unknown
        self.assertEqual(self.engine.fetch_one("SELECT * FROM vendor WHERE vendor_id = 1"), unknown_before)

    def test_changed_vendor_updates_the_prefix(self):
        load_oui_data(self.engine, [("AA:BB:CC", "Old Name Co")])
        counts = load_oui_data(self.engine, [("AA:BB:CC", "New Name Co")])
        self.assertEqual((counts["prefixes_added"], counts["prefixes_updated"]), (0, 1))
        row = self.engine.fetch_one(
            "SELECT v.vendor_name FROM oui_prefix o JOIN vendor v ON v.vendor_id = o.vendor_id "
            "WHERE o.oui_prefix = 'AA:BB:CC'")
        self.assertEqual(row["vendor_name"], "New Name Co")

    def test_names_mysql_treats_as_equal_share_one_vendor(self):
        # Python sees two names; utf8mb4_unicode_ci ignores the accent and sees one
        counts = load_oui_data(self.engine, [("AA:BB:CE", "Café Co"), ("AA:BB:CF", "Cafe Co")])
        self.assertEqual((counts["vendors_added"], counts["prefixes_added"]), (1, 2))
        rows = self.engine.fetch_all("SELECT DISTINCT vendor_id FROM oui_prefix")
        self.assertEqual(len(rows), 1)

    def test_overlong_name_is_truncated(self):
        counts = load_oui_data(self.engine, [("AA:BB:CD", "X" * 300)])
        self.assertEqual(counts["names_truncated"], 1)
        self.assertEqual(self.engine.fetch_one("SELECT LENGTH(vendor_name) AS n FROM vendor "
                                               "WHERE vendor_id <> 1")["n"], 255)
