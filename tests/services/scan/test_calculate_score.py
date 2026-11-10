"""Unit tests for 2.2.4.3 Calculate Confidence Score (spec 7.1). No database, no network.

    python -m unittest tests.services.scan.test_calculate_score
"""
from __future__ import annotations

import random
import unittest
from dataclasses import FrozenInstanceError

from backend.records.enums import EvidenceType
from backend.services.scan.calculate_score import ScoreBreakdown, calculate_score
from backend.services.scan.evidence import MAX_SCORE, EvidenceItem, count_points, weight_for
from backend.services.scan.resolve_conflicts import Resolution


def item(evidence_type: EvidenceType, raw_value: str = "x", signature_id: int | None = None,
         weight: int | None = None) -> EvidenceItem:
    """Evidence with the agreed weight for its type, unless one is given."""
    return EvidenceItem(evidence_type, raw_value, signature_id,
                        weight_for(evidence_type) if weight is None else weight)


OUI = item(EvidenceType.OUI, "F0:D1:A9")
MDNS = item(EvidenceType.MDNS, "_airplay._tcp", signature_id=1)
SSDP = item(EvidenceType.SSDP, "urn:schemas-upnp-org:device:MediaRenderer:1", signature_id=2)
NETBIOS = item(EvidenceType.NETBIOS_LLMNR, "LIVING-ROOM-TV", signature_id=3)
REVERSE_DNS = item(EvidenceType.REVERSE_DNS, "tv.home.lan", signature_id=4)


#Basic Scores
class TestScore(unittest.TestCase):
    def test_no_evidence_is_zero(self):
        breakdown = calculate_score([])
        self.assertEqual(breakdown.score, 0)
        self.assertEqual(breakdown.contributions, ())
        self.assertFalse(breakdown.capped)

    def test_oui_only_is_the_oui_weight(self):
        breakdown = calculate_score([OUI])
        self.assertEqual(breakdown.score, 20)
        self.assertEqual(len(breakdown.contributions), 1)
        self.assertEqual(breakdown.contributions[0].evidence_type, EvidenceType.OUI)
        self.assertEqual(breakdown.contributions[0].points, 20)
        self.assertEqual(breakdown.contributions[0].evidence, (OUI,))

    def test_different_types_add_up(self):
        breakdown = calculate_score([OUI, MDNS, SSDP])
        self.assertEqual(breakdown.score, 20 + 35 + 35)
        self.assertFalse(breakdown.capped)

    def test_one_type_counts_once_and_lists_every_piece(self):
        second_mdns = item(EvidenceType.MDNS, "_googlecast._tcp", signature_id=5)
        third_mdns = item(EvidenceType.MDNS, "_spotify-connect._tcp", signature_id=6)
        breakdown = calculate_score([MDNS, second_mdns, third_mdns])
        self.assertEqual(breakdown.score, 35)                  # not 105
        self.assertEqual(len(breakdown.contributions), 1)
        self.assertEqual(set(breakdown.contributions[0].evidence), {MDNS, second_mdns, third_mdns})

    def test_one_type_counts_at_its_highest_weight(self):
        weak = item(EvidenceType.MDNS, "old rule", weight=10)
        breakdown = calculate_score([weak, MDNS])
        self.assertEqual(breakdown.score, 35)
        self.assertEqual(breakdown.contributions[0].evidence[0], MDNS)   # strongest listed first

    def test_matches_count_points(self):
        evidence = [OUI, MDNS, item(EvidenceType.MDNS, "again"), NETBIOS]
        self.assertEqual(calculate_score(evidence).points, count_points(evidence))


#Cap
class TestCap(unittest.TestCase):
    def test_over_100_is_capped(self):
        breakdown = calculate_score([OUI, MDNS, SSDP, NETBIOS, REVERSE_DNS])   # 135 points
        self.assertEqual(breakdown.score, MAX_SCORE)
        self.assertTrue(breakdown.capped)
        self.assertEqual(breakdown.points, 135)

    def test_exactly_100_is_not_capped(self):
        breakdown = calculate_score([OUI, MDNS, SSDP, item(EvidenceType.NETBIOS_LLMNR, weight=10)])
        self.assertEqual(breakdown.score, 100)
        self.assertFalse(breakdown.capped)


#Traceability (spec 7.1)
class TestContributions(unittest.TestCase):
    def test_contributions_add_up_to_the_uncapped_points(self):
        for evidence in ([], [OUI], [OUI, MDNS], [OUI, MDNS, SSDP, NETBIOS, REVERSE_DNS]):
            with self.subTest(evidence=[e.evidence_type.value for e in evidence]):
                breakdown = calculate_score(evidence)
                self.assertEqual(sum(c.points for c in breakdown.contributions), count_points(evidence))

    def test_every_piece_of_evidence_is_listed_once(self):
        evidence = [OUI, MDNS, item(EvidenceType.MDNS, "again"), SSDP]
        listed = [e for c in calculate_score(evidence).contributions for e in c.evidence]
        self.assertCountEqual(listed, evidence)

    def test_contributions_come_in_a_fixed_order(self):
        evidence = [REVERSE_DNS, SSDP, OUI, NETBIOS, MDNS]
        expected = calculate_score(evidence)
        for seed in range(20):
            shuffled = evidence[:]
            random.Random(seed).shuffle(shuffled)
            with self.subTest(seed=seed):
                self.assertEqual(calculate_score(shuffled), expected)
        order = [c.evidence_type.value for c in expected.contributions]
        self.assertEqual(order, sorted(order))

    def test_breakdown_cannot_be_changed(self):
        breakdown = calculate_score([OUI])
        with self.assertRaises(FrozenInstanceError):
            breakdown.score = 99
        self.assertIsInstance(breakdown.contributions, tuple)

    def test_accepts_any_iterable(self):
        self.assertEqual(calculate_score(iter([OUI, MDNS])).score, 55)
        self.assertEqual(calculate_score((OUI,)).score, 20)


#From a Resolution (2.2.4.2)
class TestFromResolution(unittest.TestCase):
    def test_dropped_evidence_adds_nothing(self):
        resolution = Resolution(device_type_id=7, kept=(OUI, MDNS), dropped=(SSDP, NETBIOS),
                                winning_matches=())
        breakdown = calculate_score(resolution.kept)
        self.assertEqual(breakdown.score, 55)
        types = {c.evidence_type for c in breakdown.contributions}
        self.assertNotIn(EvidenceType.SSDP, types)
        self.assertNotIn(EvidenceType.NETBIOS_LLMNR, types)

    def test_a_tie_keeps_only_the_oui(self):
        resolution = Resolution(device_type_id=None, kept=(OUI,), dropped=(MDNS, SSDP),
                                winning_matches=())
        self.assertEqual(calculate_score(resolution.kept).score, 20)


#Generated Evidence
class TestGenerated(unittest.TestCase):
    def test_score_is_always_a_whole_number_from_0_to_100(self):
        rng = random.Random(4313)
        types = list(EvidenceType)
        for n in range(2000):
            evidence = [EvidenceItem(rng.choice(types), f"value {i}",
                                     rng.choice([None, rng.randint(1, 50)]),
                                     rng.choice([weight_for(t) for t in types] + [rng.randint(0, 200)]))
                        for i in range(rng.randint(0, 12))]
            breakdown = calculate_score(evidence)
            with self.subTest(n=n):
                self.assertIsInstance(breakdown, ScoreBreakdown)
                self.assertIsInstance(breakdown.score, int)
                self.assertGreaterEqual(breakdown.score, 0)
                self.assertLessEqual(breakdown.score, MAX_SCORE)
                self.assertEqual(breakdown.points, count_points(evidence))
                self.assertEqual(breakdown.capped, breakdown.points > MAX_SCORE)


if __name__ == "__main__":
    unittest.main()