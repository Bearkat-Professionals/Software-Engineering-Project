"""Unit tests for signature matching (#65). No database: signatures and values are made by hand.

    python -m unittest tests.services.scan.test_match_signatures -v
"""
import ast
import unittest
from pathlib import Path

from backend.records import DeviceSignature
from backend.records.enums import EvidenceType, RecordStatus
from backend.services.scan import match_signatures as module
from backend.services.scan.evidence import EvidenceItem, RawEvidence, weight_for
from backend.services.scan.match_signatures import SignatureMatch, match_signatures

MDNS, SSDP = EvidenceType.MDNS, EvidenceType.SSDP


def signature(signature_id, pattern, evidence_type=MDNS, status=RecordStatus.ACTIVE, **extra) -> DeviceSignature:
    return DeviceSignature(signature_id=signature_id, device_type_id=extra.pop("device_type_id", 3),
                           pattern_value=pattern, evidence_type=evidence_type, status=status, **extra)


class DeviceSignatureMatchesTests(unittest.TestCase):
    def test_case_and_surrounding_spaces_do_not_matter(self):
        self.assertTrue(signature(1, "echo").matches("  Living-Room-ECHO "))
        self.assertTrue(signature(1, "  ECHO  ").matches("my echo dot"))
        self.assertFalse(signature(1, "echo").matches("Living-Room-Dot"))

    def test_plain_text_not_regex(self):
        self.assertFalse(signature(1, "e.*o").matches("echo"))
        self.assertTrue(signature(1, "e.*o").matches("model e.*o 2"))
        self.assertFalse(signature(1, "[").matches("echo"))           # an invalid regex never raises
        self.assertTrue(signature(1, "[").matches("printer [office]"))

    def test_blank_values_and_patterns_never_match(self):
        for pattern in ("", "   ", None):
            for value in ("", "   ", None, "echo"):
                if pattern is None:
                    pattern_signature = signature(1, "echo")
                    pattern_signature.pattern_value = None
                else:
                    pattern_signature = signature(1, pattern)
                with self.subTest(pattern=pattern, value=value):
                    self.assertFalse(pattern_signature.matches(value))
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertFalse(signature(1, "echo").matches(value))


class MatchSignaturesTests(unittest.TestCase):
    def test_match_carries_everything_from_the_signature_and_the_weight(self):
        echo = signature(7, "echo", device_type_id=4, resulting_device_name="Amazon Echo", resulting_model="Dot")
        [match] = match_signatures([RawEvidence(MDNS, "Living-Room-Echo")], [echo])
        self.assertEqual(match, SignatureMatch(EvidenceItem(MDNS, "Living-Room-Echo", 7, weight_for(MDNS)),
                                               device_type_id=4, resulting_device_name="Amazon Echo",
                                               resulting_model="Dot"))

    def test_weight_follows_the_evidence_type(self):
        values = [RawEvidence(MDNS, "echo"), RawEvidence(SSDP, "echo")]
        matches = match_signatures(values, [signature(1, "echo", MDNS), signature(2, "echo", SSDP)])
        self.assertEqual([(m.evidence.evidence_type, m.evidence.weight) for m in matches],
                         [(MDNS, weight_for(MDNS)), (SSDP, weight_for(SSDP))])

    def test_different_evidence_type_does_not_match(self):
        self.assertEqual(match_signatures([RawEvidence(SSDP, "echo")], [signature(1, "echo", MDNS)]), [])

    def test_retired_signature_does_not_match(self):
        retired = signature(1, "echo", status=RecordStatus.RETIRED)
        self.assertEqual(match_signatures([RawEvidence(MDNS, "echo")], [retired]), [])

    def test_one_value_matching_two_signatures_gives_two_matches(self):
        matches = match_signatures([RawEvidence(MDNS, "Echo-Dot")], [signature(1, "echo"), signature(2, "dot")])
        self.assertEqual([m.evidence.signature_id for m in matches], [1, 2])

    def test_same_value_collected_twice_gives_one_match(self):
        values = [RawEvidence(MDNS, "Echo-Dot"), RawEvidence(MDNS, "  Echo-Dot "), RawEvidence(MDNS, "Echo-Dot")]
        [match] = match_signatures(values, [signature(1, "echo")])
        self.assertEqual(match.evidence.raw_value, "Echo-Dot")

    def test_same_value_in_different_case_gives_one_match(self):
        values = [RawEvidence(MDNS, "Echo-Dot"), RawEvidence(MDNS, "echo-dot"), RawEvidence(MDNS, " ECHO-DOT")]
        [match] = match_signatures(values, [signature(1, "echo")])
        self.assertEqual(match.evidence.raw_value, "Echo-Dot")          # the first spelling is kept

    def test_different_values_still_match_separately(self):            # the case from the #65 review
        values = [RawEvidence(MDNS, "Echo-Dot"), RawEvidence(MDNS, "echo-dot"), RawEvidence(MDNS, "Kitchen Echo")]
        matches = match_signatures(values, [signature(1, "echo")])
        self.assertEqual([m.evidence.raw_value for m in matches], ["Echo-Dot", "Kitchen Echo"])
        self.assertEqual(sum(m.evidence.weight for m in matches), 2 * weight_for(MDNS))   # was 3x before

    def test_same_text_with_a_different_evidence_type_is_kept_separately(self):
        values = [RawEvidence(MDNS, "Echo"), RawEvidence(SSDP, "echo")]
        matches = match_signatures(values, [signature(1, "echo", MDNS), signature(2, "echo", SSDP)])
        self.assertEqual([(m.evidence.evidence_type, m.evidence.raw_value) for m in matches],
                         [(MDNS, "Echo"), (SSDP, "echo")])

    def test_values_that_match_nothing_and_blank_values_are_left_out(self):
        values = [RawEvidence(MDNS, "printer"), RawEvidence(MDNS, ""), RawEvidence(MDNS, "  "),
                  RawEvidence(MDNS, None)]
        self.assertEqual(match_signatures(values, [signature(1, "echo"), signature(2, "")]), [])

    def test_sorted_by_signature_id_then_value(self):
        values = [RawEvidence(MDNS, "kitchen-echo"), RawEvidence(MDNS, "den-echo-dot"), RawEvidence(MDNS, "bed-dot")]
        matches = match_signatures(values, [signature(9, "echo"), signature(2, "dot")])
        self.assertEqual([(m.evidence.signature_id, m.evidence.raw_value) for m in matches],
                         [(2, "bed-dot"), (2, "den-echo-dot"), (9, "den-echo-dot"), (9, "kitchen-echo")])


class PurityTests(unittest.TestCase):
    def test_imports_nothing_from_the_database_network_or_other_services(self):
        # Allow-list, so any new import has to be looked at here
        allowed = {"__future__", "dataclasses", "typing", "backend.records", "backend.records.enums",
                   "backend.services.scan.evidence"}
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, allowed)


if __name__ == "__main__":
    unittest.main()
