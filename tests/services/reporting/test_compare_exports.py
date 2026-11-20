"""Tests for the NFR-08 export comparison (M7, #174). No database, no network.

    py -m unittest tests.services.reporting.test_compare_exports -v
"""
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from backend.services.reporting.compare_exports import compare_documents, compare_shapes, main, shape

WINDOWS = {"format": "network-device-export", "format_version": 1, "exported_at": "2026-11-19T20:00:00Z",
           "devices": [{"device_id": 7, "name": "Office printer", "mac": "02:00:00:00:00:07", "trusted": False,
                        "confidence": 55, "evidence": [{"type": "mdns", "weight": 35}]},
                       {"device_id": 9, "name": None, "mac": "02:00:00:00:00:09", "trusted": True,
                        "confidence": 20, "evidence": []}]}


def differences(first, second):
    return compare_shapes(shape(first), shape(second))


def linux(**changes):
    """The same document as WINDOWS with other values, then the given changes to the first device."""
    document = json.loads(json.dumps(WINDOWS))
    document["exported_at"] = "2026-11-19T21:30:00Z"
    document["devices"][0].update(name="HP Envy", confidence=90)
    document["devices"][0].update(changes)
    return document


class CompareShapesTests(unittest.TestCase):
    def test_other_values_same_structure(self):
        self.assertEqual(differences(WINDOWS, linux()), [])

    def test_null_and_a_value_are_the_same_structure(self):
        self.assertEqual(differences(WINDOWS, linux(name=None)), [])
        self.assertEqual(differences({"a": None}, {"a": {"b": 1}}), [])

    def test_different_numbers_of_items_and_empty_lists(self):
        fewer = linux()
        fewer["devices"] = fewer["devices"][:1]
        self.assertEqual(differences(WINDOWS, fewer), [])
        self.assertEqual(differences(WINDOWS, dict(linux(), devices=[])), [])

    def test_a_missing_or_extra_key(self):
        changed = linux()
        del changed["devices"][0]["mac"]
        del changed["devices"][1]["mac"]
        changed["devices"][0]["is_gateway"] = False
        self.assertEqual(differences(WINDOWS, changed), [
            "devices[]: only in the first file: mac",
            "devices[]: only in the second file: is_gateway"])

    def test_the_same_keys_in_another_order(self):
        reordered = {"format_version": 1, "format": "x", "exported_at": "y", "devices": []}
        self.assertEqual(differences(WINDOWS, reordered),
                         ["(top level): keys in a different order: "
                          "['format', 'format_version', 'exported_at', 'devices'] vs "
                          "['format_version', 'format', 'exported_at', 'devices']"])

    def test_a_different_type(self):
        for value, expected in (("55", "number in the first file, string in the second file"),
                                (True, "number in the first file, boolean in the second file"),
                                ([55], "number in the first file, list in the second file"),
                                ({"v": 55}, "number in the first file, object in the second file")):
            with self.subTest(value=value):
                both = linux(confidence=value)
                both["devices"][1]["confidence"] = value
                self.assertEqual(differences(WINDOWS, both), [f"devices[].confidence: {expected}"])

    def test_nested_lists_are_compared_too(self):
        changed = linux(evidence=[{"type": "mdns", "weight": "35"}])
        self.assertEqual(differences(WINDOWS, changed),
                         ["devices[].evidence[].weight: number in the first file, string in the second file"])


def device(device_id, **marks):
    return {"device_id": device_id, "name": None,
            "user_marks": marks or {"trusted": False, "flagged": True, "flag_set_by_user": False}}


def export(*devices):
    return {"format": "network-device-export", "devices": list(devices)}


class ItemsInsideOneFileTests(unittest.TestCase):
    """The union of item shapes would hide an odd item, so each file's items are checked
    against its first non-null item first (review on #199)."""
    GOOD = export(device(1), device(2), device(3))

    def test_matching_items_are_fine(self):
        self.assertEqual(compare_documents(self.GOOD, self.GOOD), [])

    def test_a_key_missing_in_a_later_item(self):
        odd = export(device(1), device(2), device(3, trusted=False, flag_set_by_user=False))
        self.assertEqual(compare_documents(self.GOOD, odd),
                         ["second file: devices[2].user_marks: only in devices[0]: flagged"])

    def test_an_extra_key_in_a_later_item(self):
        odd = export(device(1), dict(device(2), is_gateway=True))
        self.assertEqual(compare_documents(odd, self.GOOD),
                         ["first file: devices[1]: only in devices[1]: is_gateway",
                          "devices[]: only in the first file: is_gateway"])

    def test_a_later_items_keys_in_a_different_order(self):
        odd = export(device(1), device(2, flagged=True, trusted=False, flag_set_by_user=False))
        self.assertEqual(compare_documents(self.GOOD, odd),
                         ["second file: devices[1].user_marks: keys in a different order: "
                          "['trusted', 'flagged', 'flag_set_by_user'] vs "
                          "['flagged', 'trusted', 'flag_set_by_user']"])

    def test_a_later_items_type(self):
        odd = export(device(1), dict(device(2), device_id="2"))
        self.assertEqual(compare_documents(self.GOOD, odd),
                         ["second file: devices[1].device_id: number in devices[0], string in devices[1]"])

    def test_null_items_and_null_values_are_fine(self):
        named = dict(device(2), name="Office printer")
        self.assertEqual(compare_documents(self.GOOD, export(None, device(1), named, None)), [])

    def test_the_first_non_null_item_is_the_reference(self):
        odd = export(None, device(1), dict(device(2), is_gateway=True))
        self.assertEqual(compare_documents(self.GOOD, odd),
                         ["second file: devices[2]: only in devices[2]: is_gateway",
                          "devices[]: only in the second file: is_gateway"])

    def test_lists_inside_items_are_checked_too(self):
        good = export(dict(device(1), evidence=[{"type": "mdns", "weight": 35}, {"type": "oui", "weight": 20}]))
        odd = export(dict(device(1), evidence=[{"type": "mdns", "weight": 35}, {"type": "oui"}]))
        self.assertEqual(compare_documents(good, odd),
                         ["second file: devices[0].evidence[1]: only in devices[0].evidence[0]: weight"])


class MainTests(unittest.TestCase):
    def run_main(self, first, second, bom=False):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            for name, document in (("windows.json", first), ("linux.json", second)):
                path = Path(folder) / name
                path.write_text(("﻿" if bom else "") + json.dumps(document), encoding="utf-8")
                paths.append(str(path))
            out = StringIO()
            with redirect_stdout(out):
                code = main(paths)
        return code, out.getvalue()

    def test_same_structure_exits_0(self):
        code, out = self.run_main(WINDOWS, linux())
        self.assertEqual(code, 0)
        self.assertIn("Same structure", out)

    def test_a_difference_exits_1_and_says_where(self):
        code, out = self.run_main(WINDOWS, linux(confidence="90"))
        self.assertEqual(code, 1)
        self.assertIn("devices[].confidence", out)

    def test_an_odd_item_the_union_would_hide_exits_1(self):
        odd = linux()
        del odd["devices"][1]["mac"]
        code, out = self.run_main(WINDOWS, odd)
        self.assertEqual(code, 1)
        self.assertIn("second file: devices[1]: only in devices[0]: mac", out)

    def test_a_bom_from_a_windows_editor_is_fine(self):
        self.assertEqual(self.run_main(WINDOWS, linux(), bom=True)[0], 0)

    def test_an_unreadable_file_exits_2(self):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["no-such-file.json", "no-such-file-either.json"]), 2)
        self.assertIn("Couldn't read the exports", out.getvalue())


if __name__ == "__main__":
    unittest.main()