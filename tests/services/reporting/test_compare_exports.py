"""Tests for the NFR-08 export comparison (M7, #174). No database, no network.

    py -m unittest tests.services.reporting.test_compare_exports -v
"""
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from backend.services.reporting.compare_exports import compare_shapes, main, shape

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
        for value, expected in (("55", "number in the first file, string in the second"),
                                (True, "number in the first file, boolean in the second"),
                                ([55], "number in the first file, list in the second"),
                                ({"v": 55}, "number in the first file, object in the second")):
            with self.subTest(value=value):
                both = linux(confidence=value)
                both["devices"][1]["confidence"] = value
                self.assertEqual(differences(WINDOWS, both), [f"devices[].confidence: {expected}"])

    def test_nested_lists_are_compared_too(self):
        changed = linux(evidence=[{"type": "mdns", "weight": "35"}])
        self.assertEqual(differences(WINDOWS, changed),
                         ["devices[].evidence[].weight: number in the first file, string in the second"])

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

    def test_a_bom_from_a_windows_editor_is_fine(self):
        self.assertEqual(self.run_main(WINDOWS, linux(), bom=True)[0], 0)

    def test_an_unreadable_file_exits_2(self):
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["no-such-file.json", "no-such-file-either.json"]), 2)
        self.assertIn("Couldn't read the exports", out.getvalue())

if __name__ == "__main__":
    unittest.main()