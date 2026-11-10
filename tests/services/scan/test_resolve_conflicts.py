# tests/services/scan/test_resolve_conflicts.py
"""Unit tests for 2.2.4.2 Resolve Conflicting Evidence (#66). No database or network:
the matches are made by hand.

    python -m unittest tests.services.scan.test_resolve_conflicts -v
"""
from __future__ import annotations

import ast
import random
import unittest
from pathlib import Path

from backend.records.enums import EvidenceType
from backend.services.scan import resolve_conflicts as module
from backend.services.scan.evidence import EvidenceItem, weight_for
from backend.services.scan.match_signatures import SignatureMatch
from backend.services.scan.resolve_conflicts import Resolution, resolve_conflicts

MDNS, SSDP, NETBIOS, OUI = (EvidenceType.MDNS, EvidenceType.SSDP, EvidenceType.NETBIOS_LLMNR,
                            EvidenceType.OUI)
SPEAKER, PRINTER, CAMERA = 4, 7, 9          # device type ids
OUI_ITEM = EvidenceItem(OUI, "F0:D1:A9", None, weight_for(OUI))


def match(device_type_id: int, evidence_type: EvidenceType, value: str, signature_id: int,
          name: str | None = None) -> SignatureMatch:
    return SignatureMatch(EvidenceItem(evidence_type, value, signature_id, weight_for(evidence_type)),
                          device_type_id, resulting_device_name=name, resulting_model=None)


def evidence(*matches: SignatureMatch) -> set[EvidenceItem]:
    return {found.evidence for found in matches}


class NoConflictTests(unittest.TestCase):
    def test_no_signature_matches_gives_no_type_and_keeps_the_oui(self) -> None:
        self.assertEqual(resolve_conflicts([], [OUI_ITEM]), Resolution(None, (OUI_ITEM,), (), ()))

    def test_nothing_at_all(self) -> None:
        self.assertEqual(resolve_conflicts([], []), Resolution(None, (), (), ()))

    def test_one_type_wins_and_everything_is_kept(self) -> None:
        echo = match(SPEAKER, MDNS, "Echo-Dot", 1, name="Amazon Echo")
        upnp = match(SPEAKER, SSDP, "urn:amazon:echo", 2)
        result = resolve_conflicts([echo, upnp], [OUI_ITEM])
        self.assertEqual(result.device_type_id, SPEAKER)
        self.assertEqual(set(result.kept), evidence(echo, upnp) | {OUI_ITEM})
        self.assertEqual(result.dropped, ())
        self.assertEqual(set(result.winning_matches), {echo, upnp})


class ConflictTests(unittest.TestCase):
    def test_the_stronger_type_wins_and_the_weaker_is_dropped(self) -> None:
        printer = [match(PRINTER, MDNS, "HP-LaserJet", 3), match(PRINTER, SSDP, "urn:hp:printer", 4)]   # 70
        camera = [match(CAMERA, NETBIOS, "CAM-01", 5)]                                                # 25
        result = resolve_conflicts([*printer, *camera], [OUI_ITEM])
        self.assertEqual(result.device_type_id, PRINTER)
        self.assertEqual(set(result.kept), evidence(*printer) | {OUI_ITEM})
        self.assertEqual(set(result.dropped), evidence(*camera))
        self.assertEqual(set(result.winning_matches), set(printer))

    def test_each_evidence_type_counts_once_when_comparing(self) -> None:
        # A: three mDNS matches = 35 (not 105). B: mDNS + SSDP = 70. B wins.
        speaker = [match(SPEAKER, MDNS, name, 1) for name in ("echo", "echo-dot", "kitchen-echo")]
        printer = [match(PRINTER, MDNS, "HP-LaserJet", 3), match(PRINTER, SSDP, "urn:hp:printer", 4)]
        result = resolve_conflicts([*speaker, *printer], [])
        self.assertEqual(result.device_type_id, PRINTER)
        self.assertEqual(set(result.dropped), evidence(*speaker))

    def test_a_tie_gives_no_type_and_drops_all_signature_evidence(self) -> None:
        speaker = match(SPEAKER, MDNS, "living-room", 1)       # 35
        printer = match(PRINTER, SSDP, "urn:hp:printer", 4)    # 35
        result = resolve_conflicts([speaker, printer], [OUI_ITEM])
        self.assertIsNone(result.device_type_id)
        self.assertEqual(result.kept, (OUI_ITEM,))
        self.assertEqual(set(result.dropped), evidence(speaker, printer))
        self.assertEqual(result.winning_matches, ())

    def test_a_tie_at_the_top_still_ties_when_a_weaker_third_type_exists(self) -> None:
        tied = [match(SPEAKER, MDNS, "a", 1), match(PRINTER, SSDP, "b", 4)]
        weaker = match(CAMERA, NETBIOS, "c", 5)
        result = resolve_conflicts([*tied, weaker], [])
        self.assertIsNone(result.device_type_id)
        self.assertEqual(set(result.dropped), evidence(*tied, weaker))

    def test_one_value_matching_two_types_is_a_tie(self) -> None:
        # The same mDNS name matched a speaker signature and a camera signature
        both = [match(SPEAKER, MDNS, "smart-home-hub", 1), match(CAMERA, MDNS, "smart-home-hub", 6)]
        self.assertIsNone(resolve_conflicts(both, []).device_type_id)


class OrderTests(unittest.TestCase):
    def test_shuffled_input_gives_the_same_result(self) -> None:
        matches = [match(PRINTER, MDNS, "HP-LaserJet", 3), match(PRINTER, SSDP, "urn:hp:printer", 4),
                   match(CAMERA, NETBIOS, "CAM-01", 5), match(SPEAKER, MDNS, "echo", 1)]
        other = [OUI_ITEM, EvidenceItem(EvidenceType.REVERSE_DNS, "printer.local", None, 20)]
        expected = resolve_conflicts(matches, other)
        shuffler = random.Random(66)                       # fixed seed: the same shuffles every run
        for attempt in range(20):
            shuffler.shuffle(matches)
            shuffler.shuffle(other)
            with self.subTest(attempt=attempt):
                self.assertEqual(resolve_conflicts(matches, other), expected)

    def test_accepts_one_time_iterables(self) -> None:
        printer = match(PRINTER, MDNS, "HP-LaserJet", 3)
        result = resolve_conflicts(iter([printer]), iter([OUI_ITEM]))
        self.assertEqual((result.device_type_id, len(result.kept)), (PRINTER, 2))


class PurityTests(unittest.TestCase):
    def test_imports_nothing_from_the_database_network_or_other_services(self) -> None:
        allowed = {"__future__", "collections", "collections.abc", "dataclasses",
                   "backend.services.scan.evidence", "backend.services.scan.match_signatures"}
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertLessEqual(imported, allowed)


if __name__ == "__main__":
    unittest.main()
