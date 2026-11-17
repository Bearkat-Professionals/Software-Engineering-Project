"""Unit tests for the evidence weights and EvidenceItem (#64, spec 7.1). No database.

    python -m unittest tests.services.scan.test_evidence -v
"""
import unittest

from backend.records import IdentificationEvidence
from backend.records.enums import EvidenceType
from backend.services.scan.evidence import (EVIDENCE_WEIGHTS, FIELD_MAX, IDENTIFIED_MIN_SCORE, MAX_SCORE,
                                            EvidenceItem, clean_field, count_points, weight_for)


class EvidenceWeightTests(unittest.TestCase):
    def test_every_evidence_type_has_a_weight(self):
        # A new EvidenceType without a weight fails here
        self.assertEqual(set(EVIDENCE_WEIGHTS), set(EvidenceType))
        for evidence_type in EvidenceType:
            self.assertEqual(weight_for(evidence_type), EVIDENCE_WEIGHTS[evidence_type])

    def test_every_weight_is_a_whole_number_in_range(self):
        for evidence_type, weight in EVIDENCE_WEIGHTS.items():
            with self.subTest(evidence_type=evidence_type):
                self.assertIs(type(weight), int)
                self.assertGreater(weight, 0)
                self.assertLessEqual(weight, MAX_SCORE)

    def test_threshold_is_within_the_score_range(self):
        self.assertEqual(MAX_SCORE, 100)
        self.assertGreaterEqual(IDENTIFIED_MIN_SCORE, 1)
        self.assertLessEqual(IDENTIFIED_MIN_SCORE, MAX_SCORE)


class EvidenceItemTests(unittest.TestCase):
    def test_to_record_and_back(self):
        item = EvidenceItem(EvidenceType.SSDP, "urn:schemas-upnp-org:device:MediaRenderer:1", 7,
                            weight_for(EvidenceType.SSDP))
        record = item.to_record(device_id=42)
        self.assertIsInstance(record, IdentificationEvidence)
        self.assertEqual((record.device_id, record.evidence_id), (42, None))
        self.assertEqual(EvidenceItem.from_record(record), item)

    def test_from_stored_row_without_signature(self):
        stored = IdentificationEvidence(evidence_id=5, device_id=3, evidence_type=EvidenceType.OUI,
                                        raw_value="70:13:01", weight=weight_for(EvidenceType.OUI))
        item = EvidenceItem.from_record(stored)
        self.assertEqual(item, EvidenceItem(EvidenceType.OUI, "70:13:01", None, weight_for(EvidenceType.OUI)))
        self.assertEqual(item.to_record(3).raw_value, "70:13:01")


class CountPointsTests(unittest.TestCase):
    MDNS, SSDP, OUI = EvidenceType.MDNS, EvidenceType.SSDP, EvidenceType.OUI

    def item(self, evidence_type: EvidenceType, value: str, signature_id: int | None = None) -> EvidenceItem:
        return EvidenceItem(evidence_type, value, signature_id, weight_for(evidence_type))

    def test_nothing_is_worth_nothing(self) -> None:
        self.assertEqual(count_points([]), 0)

    def test_one_evidence_type_counts_once(self) -> None:
        three_mdns = [self.item(self.MDNS, name, 1) for name in ("echo", "echo-dot", "kitchen-echo")]
        self.assertEqual(count_points(three_mdns), weight_for(self.MDNS))

    def test_different_types_are_added_together(self) -> None:
        items = [self.item(self.MDNS, "echo", 1), self.item(self.SSDP, "echo", 2), self.item(self.OUI, "F0:D1:A9")]
        self.assertEqual(count_points(items),
                         weight_for(self.MDNS) + weight_for(self.SSDP) + weight_for(self.OUI))

    def test_a_type_counts_at_its_highest_weight(self) -> None:
        # Stored evidence keeps the weight it was saved with, which may be older than today's table
        items = [EvidenceItem(self.MDNS, "old", 1, 10), EvidenceItem(self.MDNS, "new", 1, 35)]
        self.assertEqual(count_points(items), 35)

    def test_it_is_not_capped(self) -> None:
        items = [EvidenceItem(evidence_type, "x", None, 60) for evidence_type in (self.MDNS, self.SSDP)]
        self.assertEqual(count_points(items), 120)          # the score caps at MAX_SCORE, not this

    def test_works_on_any_iterable(self) -> None:
        self.assertEqual(count_points(self.item(self.MDNS, name) for name in ("a", "b")), weight_for(self.MDNS))


class CleanFieldTests(unittest.TestCase):
    """Text from devices (#147): safe to store and show, whatever was sent."""

    def test_control_characters_and_whitespace_become_one_space(self) -> None:
        self.assertEqual(clean_field("  Living\r\nRoom\x00\x07 TV\x9b "), "Living Room TV")

    def test_nothing_left_is_none(self) -> None:
        for value in ["", "   ", "\t\r\n", "\x00\x1f", None, 42]:
            with self.subTest(value=value):
                self.assertIsNone(clean_field(value))

    def test_cut_to_the_limit(self) -> None:
        self.assertEqual(len(clean_field("x" * 1000)), FIELD_MAX)
        self.assertEqual(clean_field("abc def", limit=4), "abc")       # no space left dangling

    def test_ordinary_text_is_kept(self) -> None:
        self.assertEqual(clean_field("Café Bar; Model=X"), "Café Bar; Model=X")


if __name__ == "__main__":
    unittest.main()
