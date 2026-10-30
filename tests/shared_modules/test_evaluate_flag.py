"""Tests for SH-4.1 Evaluate Flag (spec 7.3). No database or network needed.

    python -m unittest discover tests
"""
import ast
import inspect
import unittest

from backend.records.enums import IdentificationLevel
from backend.shared_modules import evaluate_flag as module
from backend.shared_modules.evaluate_flag import evaluate_flag

IDENTIFIED = IdentificationLevel.IDENTIFIED
PARTIAL = IdentificationLevel.PARTIAL
UNIDENTIFIED = IdentificationLevel.UNIDENTIFIED

# Every combination: 3 levels x trusted yes/no x new device yes/no = 12 cases
#   level         trusted  new    flagged  rule it checks
CASES = [
    (IDENTIFIED,   False,   False,  False,  "rule 2: identified is not flagged"),
    (IDENTIFIED,   False,   True,   False,  "rule 2: identified is not flagged, even when new"),
    (IDENTIFIED,   True,    False,  False,  "rules 2 and 3: identified and trusted"),
    (IDENTIFIED,   True,    True,   False,  "rule 2: identified is not flagged, even with a new MAC"),
    (PARTIAL,      False,   False,  True,   "rule 1: partially identified is flagged"),
    (PARTIAL,      False,   True,   True,   "rule 1: partially identified is flagged when new"),
    (PARTIAL,      True,    False,  False,  "rule 3: trusted is not re-flagged"),
    (PARTIAL,      True,    True,   True,   "rule 3: a new MAC is flagged again despite trust"),
    (UNIDENTIFIED, False,   False,  True,   "rule 1: unidentified is flagged"),
    (UNIDENTIFIED, False,   True,   True,   "rule 1: unidentified is flagged when new"),
    (UNIDENTIFIED, True,    False,  False,  "rule 3: trusted is not re-flagged"),
    (UNIDENTIFIED, True,    True,   True,   "rule 3: a new MAC is flagged again despite trust"),
]


class EvaluateFlagTests(unittest.TestCase):
    def test_every_combination(self):
        self.assertEqual(len(CASES), 12)
        for level, trusted, new, expected, rule in CASES:
            with self.subTest(rule, level=level.value, trusted=trusted, new=new):
                self.assertIs(evaluate_flag(level, trusted, new), expected)

    def test_the_level_can_be_given_as_text(self):
        self.assertTrue(evaluate_flag("partial", trusted=False, is_new_device=False))
        self.assertFalse(evaluate_flag("identified", trusted=False, is_new_device=False))

    def test_an_unknown_level_is_refused(self):
        with self.assertRaises(ValueError):
            evaluate_flag("mostly identified", trusted=False, is_new_device=False)

    def test_imports_nothing_from_the_database_network_or_services(self):
        tree = ast.parse(inspect.getsource(module))
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                     for alias in node.names}
        self.assertEqual(imported, {"backend.records.enums"})


if __name__ == "__main__":
    unittest.main()
