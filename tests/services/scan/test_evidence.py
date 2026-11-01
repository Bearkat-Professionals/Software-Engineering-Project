"""Unit tests for the evidence weights and EvidenceItem (#64, spec 7.1). No database.

    python -m unittest tests.services.scan.test_evidence -v
"""
import unittest

from backend.records import IdentificationEvidence
from backend.records.enums import EvidenceType
from backend.services.scan.evidence import (EVIDENCE_WEIGHTS, IDENTIFIED_MIN_SCORE, MAX_SCORE, EvidenceItem,
                                            weight_for)


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


if __name__ == "__main__":
    unittest.main()
