"""Unit tests for OUI matching (#38). No database: the lookup is built by hand.

    python -m unittest tests.services.scan.test_match_oui -v
"""
import unittest

from backend.data_access.reference_data import ReferenceRules
from backend.records import OuiPrefix, Vendor
from backend.records.enums import EvidenceType, RecordStatus
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.match_oui import (OUI_EVIDENCE_WEIGHT, EvidenceItem, OuiMatch,
                                             build_oui_lookup, match_oui, oui_lookup_from_rules)

VENDORS = [
    Vendor(vendor_id=2, vendor_name="Vantiva - Connected Home"),
    Vendor(vendor_id=3, vendor_name="Retired Co", status=RecordStatus.RETIRED),
    Vendor(vendor_id=4, vendor_name="Local Bit Co"),
]
PREFIXES = [
    OuiPrefix(oui_prefix="70:13:01", vendor_id=2),
    OuiPrefix(oui_prefix="00:11:22", vendor_id=3),
    OuiPrefix(oui_prefix="02:11:22", vendor_id=4),     # in the lookup, but locally administered
    OuiPrefix(oui_prefix="0B:11:22", vendor_id=4),     # in the lookup, but locally administered + multicast
]


def host(mac, randomized=False) -> DiscoveredHost:
    return DiscoveredHost("10.0.0.1", mac, randomized)


class MatchOuiTests(unittest.TestCase):
    def setUp(self):
        self.lookup = build_oui_lookup(VENDORS, PREFIXES)

    def test_known_prefix_matches_with_exact_evidence(self):
        match = match_oui(host("70:13:01:65:8E:F7"), self.lookup)
        self.assertEqual(match, OuiMatch(
            vendor_id=2, vendor_name="Vantiva - Connected Home",
            evidence=EvidenceItem(evidence_type=EvidenceType.OUI, raw_value="70:13:01",
                                  signature_id=None, weight=OUI_EVIDENCE_WEIGHT)))
        self.assertEqual(OUI_EVIDENCE_WEIGHT, 20)

    def test_other_mac_formats_are_normalized(self):
        for mac in ("70:13:01:65:8e:f7", "70-13-01-65-8E-F7", "70-13-01-65-8e-f7",
                    "7013.0165.8ef7", " 70:13:01:65:8E:F7 "):
            with self.subTest(mac=mac):
                self.assertEqual(match_oui(host(mac), self.lookup).evidence.raw_value, "70:13:01")

    def test_unknown_prefix(self):
        self.assertIsNone(match_oui(host("00:00:5E:00:53:01"), self.lookup))

    def test_randomized_flag_gives_no_match(self):
        self.assertIsNone(match_oui(host("70:13:01:65:8E:F7", randomized=True), self.lookup))

    def test_locally_administered_mac_counts_as_randomized(self):    # design 10.4, same rule as 2.2.2.5
        self.assertIn("02:11:22", self.lookup)
        self.assertIn("0B:11:22", self.lookup)
        for mac in ("02:11:22:33:44:55", "06:11:22:33:44:55", "DA:11:22:33:44:55", "fe:11:22:33:44:55",
                    "0B:11:22:33:44:55", "37:11:22:33:44:55"):
            with self.subTest(mac=mac):
                self.assertIsNone(match_oui(host(mac, randomized=False), self.lookup))

    def test_no_mac(self):
        self.assertIsNone(match_oui(host(None), self.lookup))
        self.assertIsNone(match_oui(host(""), self.lookup))

    def test_malformed_mac(self):
        for mac in ("70:13:01", "70:13:01:65:8E", "70:13:01:65:8E:F7:00", "70:13:01:65:8E:ZZ",
                    "701301658EF7", "70.13.01.65.8E.F7", "not a mac"):
            with self.subTest(mac=mac):
                self.assertIsNone(match_oui(host(mac), self.lookup))

    def test_retired_vendor_is_left_out(self):                     # DI-06, AT-08
        self.assertNotIn("00:11:22", self.lookup)
        self.assertIsNone(match_oui(host("00:11:22:33:44:55"), self.lookup))


class LookupFromRulesTests(unittest.TestCase):
    """oui_lookup_from_rules turns 3.2.1's ReferenceRules into the lookup (no database)."""

    def test_rules_become_the_lookup(self):
        vantiva = Vendor(vendor_id=2, vendor_name="Vantiva - Connected Home")
        roku = Vendor(vendor_id=5, vendor_name="Roku, Inc")
        rules = ReferenceRules(vendors={2: vantiva, 5: roku},
                               prefixes={"70:13:01": 2, "D4:BE:DC": 5, "AA:AA:AA": 99},  # 99: not active
                               device_types={}, signatures=())
        self.assertEqual(oui_lookup_from_rules(rules), {
            "70:13:01": (2, "Vantiva - Connected Home"),
            "D4:BE:DC": (5, "Roku, Inc"),
        })

    def test_empty_rules(self):
        self.assertEqual(oui_lookup_from_rules(ReferenceRules({}, {}, {}, ())), {})


if __name__ == "__main__":
    unittest.main()
