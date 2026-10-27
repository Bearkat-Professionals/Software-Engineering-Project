"""Unit tests for OUI matching (#38). No database: the lookup is built by hand.

    python -m unittest discover tests
"""
import unittest
from dataclasses import dataclass

from backend.records import OuiPrefix, Vendor
from backend.records.enums import EvidenceType, RecordStatus
from backend.services.scan.match_oui import (OUI_EVIDENCE_WEIGHT, EvidenceItem, OuiMatch,
                                             build_oui_lookup, match_oui)


@dataclass(frozen=True)
class FakeHost:     # stands in for DiscoveredHost (#23)
    mac_address: str | None
    is_randomized_mac: bool = False


VENDORS = [
    Vendor(vendor_id=2, vendor_name="Vantiva - Connected Home"),
    Vendor(vendor_id=3, vendor_name="Retired Co", status=RecordStatus.RETIRED),
    Vendor(vendor_id=4, vendor_name="Local Bit Co"),
]
PREFIXES = [
    OuiPrefix(oui_prefix="70:13:01", vendor_id=2),
    OuiPrefix(oui_prefix="00:11:22", vendor_id=3),
    OuiPrefix(oui_prefix="02:11:22", vendor_id=4),     # in the lookup, but locally administered
]


class MatchOuiTests(unittest.TestCase):
    def setUp(self):
        self.lookup = build_oui_lookup(VENDORS, PREFIXES)

    def test_known_prefix_matches_with_exact_evidence(self):
        match = match_oui(FakeHost("70:13:01:65:8E:F7"), self.lookup)
        self.assertEqual(match, OuiMatch(
            vendor_id=2, vendor_name="Vantiva - Connected Home",
            evidence=EvidenceItem(evidence_type=EvidenceType.OUI, raw_value="70:13:01",
                                  signature_id=None, weight=OUI_EVIDENCE_WEIGHT)))
        self.assertEqual(OUI_EVIDENCE_WEIGHT, 20)

    def test_lowercase_and_dashed_macs_are_normalized(self):
        for mac in ("70:13:01:65:8e:f7", "70-13-01-65-8E-F7", "70-13-01-65-8e-f7"):
            with self.subTest(mac=mac):
                self.assertEqual(match_oui(FakeHost(mac), self.lookup).evidence.raw_value, "70:13:01")

    def test_unknown_prefix(self):
        self.assertIsNone(match_oui(FakeHost("00:00:5E:00:53:01"), self.lookup))

    def test_randomized_flag_gives_no_match(self):
        self.assertIsNone(match_oui(FakeHost("70:13:01:65:8E:F7", is_randomized_mac=True), self.lookup))

    def test_locally_administered_mac_counts_as_randomized(self):    # design 10.4
        self.assertIn("02:11:22", self.lookup)
        for mac in ("02:11:22:33:44:55", "06:11:22:33:44:55", "DA:11:22:33:44:55", "fe:11:22:33:44:55"):
            with self.subTest(mac=mac):
                self.assertIsNone(match_oui(FakeHost(mac, is_randomized_mac=False), self.lookup))

    def test_no_mac(self):
        self.assertIsNone(match_oui(FakeHost(None), self.lookup))
        self.assertIsNone(match_oui(FakeHost(""), self.lookup))

    def test_malformed_mac(self):
        for mac in ("70:13:01", "70:13:01:65:8E", "70:13:01:65:8E:F7:00", "70:13:01:65:8E:ZZ",
                    "701301658EF7", "70.13.01.65.8E.F7"):
            with self.subTest(mac=mac):
                self.assertIsNone(match_oui(FakeHost(mac), self.lookup))

    def test_retired_vendor_is_left_out(self):                     # DI-06, AT-08
        self.assertNotIn("00:11:22", self.lookup)
        self.assertIsNone(match_oui(FakeHost("00:11:22:33:44:55"), self.lookup))
