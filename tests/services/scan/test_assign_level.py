import ast
import unittest
from pathlib import Path

from backend.services.scan import assign_level as module
from backend.data_access.reference_data.reference_store import UNKNOWN_ID
from backend.records.enums import EvidenceType, IdentificationLevel
from backend.services.scan.assign_level import assign_level
from backend.services.scan.calculate_score import ScoreBreakdown
from backend.services.scan.evidence import EvidenceItem, weight_for
from backend.services.scan.evidence import IDENTIFIED_MIN_SCORE, UNKNOWN_TYPE_ID, UNKNOWN_VENDOR_ID
from backend.services.scan.match_signatures import SignatureMatch
from backend.services.scan.resolve_conflicts import Resolution

Streaming_Device = 4
Amazon = 12


def make_match(evidence_type, signature_id, name=None, model=None):
    evidence = EvidenceItem(evidence_type, "value", signature_id, weight_for(evidence_type))
    return SignatureMatch(evidence, Streaming_Device, name, model)


def make_resolution(device_type_id, matches):
    return Resolution(device_type_id, (), (), tuple(matches))


def make_score(points):
    return ScoreBreakdown(points, (), False)


class TestAssignLevel(unittest.TestCase):

    def test_no_evidence(self):
        result = assign_level(make_resolution(None, []), make_score(0), None)
        self.assertEqual(result.level, IdentificationLevel.UNIDENTIFIED)
        self.assertEqual(result.device_type_id, UNKNOWN_TYPE_ID)
        self.assertEqual(result.vendor_id, UNKNOWN_VENDOR_ID)
        self.assertEqual(result.device_name, None)

    def test_oui_vendor_only(self):
        result = assign_level(make_resolution(None, []), make_score(20), Amazon)
        self.assertEqual(result.level, IdentificationLevel.PARTIAL)
        self.assertEqual(result.device_type_id, UNKNOWN_TYPE_ID)

    def test_type_but_no_name(self):
        matches = [make_match(EvidenceType.MDNS, 1)]
        result = assign_level(make_resolution(Streaming_Device, matches), make_score(90), None)
        self.assertEqual(result.level, IdentificationLevel.PARTIAL)

    def test_type_and_name_at_the_threshold(self):
        matches = [make_match(EvidenceType.MDNS, 1, "Fire TV Stick")]
        result = assign_level(make_resolution(Streaming_Device, matches), make_score(IDENTIFIED_MIN_SCORE), Amazon)
        self.assertEqual(result.level, IdentificationLevel.IDENTIFIED)
        self.assertEqual(result.device_name, "Fire TV Stick")

    def test_type_and_name_one_below_the_threshold(self):
        matches = [make_match(EvidenceType.MDNS, 1, "Fire TV Stick")]
        result = assign_level(make_resolution(Streaming_Device, matches), make_score(IDENTIFIED_MIN_SCORE - 1), Amazon)
        self.assertEqual(result.level, IdentificationLevel.PARTIAL)

    def test_tied_types_with_oui_vendor(self):
        streamer = make_match(EvidenceType.MDNS, 1, "Fire TV Stick")
        printer = make_match(EvidenceType.SSDP, 2, "HP LaserJet")
        tie = Resolution(None, (), (streamer.evidence, printer.evidence), ())
        result = assign_level(tie, make_score(20), Amazon)
        self.assertEqual(result.level, IdentificationLevel.PARTIAL)
        self.assertEqual(result.device_type_id, UNKNOWN_TYPE_ID)

    def test_two_names_order_does_not_matter(self):
        weak = make_match(EvidenceType.NETBIOS_LLMNR, 1, "FIRETV-1")
        strong = make_match(EvidenceType.MDNS, 3, "Fire TV Stick")
        same_weight = make_match(EvidenceType.SSDP, 8, "Fire TV Cube")

        first_order = [weak, strong, same_weight]
        second_order = [same_weight, strong, weak]

        result1 = assign_level(make_resolution(Streaming_Device, first_order), make_score(90), Amazon)
        result2 = assign_level(make_resolution(Streaming_Device, second_order), make_score(90), Amazon)
        self.assertEqual(result1.device_name, "Fire TV Stick")
        self.assertEqual(result2.device_name, "Fire TV Stick")

    def test_model_is_not_taken_from_the_next_match(self):
        named = make_match(EvidenceType.MDNS, 1, "Fire TV Stick")
        has_model = make_match(EvidenceType.NETBIOS_LLMNR, 2, None, "4K Max")
        result = assign_level(make_resolution(Streaming_Device, [named, has_model]), make_score(90), Amazon)
        self.assertEqual(result.model, None)

    def test_unknown_ids_match_reference_data(self):
        self.assertEqual(UNKNOWN_TYPE_ID, UNKNOWN_ID)
        self.assertEqual(UNKNOWN_VENDOR_ID, UNKNOWN_ID)




class TestPurity(unittest.TestCase):

    def test_imports_nothing_from_the_database_network_or_other_services(self):
        allowed = ["__future__", "collections.abc", "dataclasses", "backend.records.enums",
                   "backend.services.scan.calculate_score", "backend.services.scan.evidence",
                   "backend.services.scan.match_signatures", "backend.services.scan.resolve_conflicts"]

        code = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(code)

        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append(alias.name)
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module)

        for name in imported:
            self.assertIn(name, allowed)





if __name__ == "__main__":
    unittest.main()