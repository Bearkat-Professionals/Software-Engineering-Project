"""Unit tests for the Identification Manager (2.2.4, spec 7.1-7.3). No database or network.

    python -m unittest tests.services.scan.test_identification_manager -v
"""
from __future__ import annotations

import unittest

from backend.data_access.reference_data import ReferenceRules
from backend.records import DeviceSignature, DeviceType, Vendor
from backend.records.enums import EvidenceType, IdentificationLevel
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.evidence import UNKNOWN_TYPE_ID, UNKNOWN_VENDOR_ID, EvidenceItem, RawEvidence, weight_for
from backend.services.scan.identification_manager import IdentificationResult, IdentificationRules, identify

MDNS, SSDP, OUI = EvidenceType.MDNS, EvidenceType.SSDP, EvidenceType.OUI
VANTIVA = Vendor(vendor_id=2, vendor_name="Vantiva - Connected Home")
SPEAKER = DeviceType(device_type_id=3, category_name="smart speaker")
TV = DeviceType(device_type_id=4, category_name="tv")

ECHO_MDNS = DeviceSignature(signature_id=10, device_type_id=SPEAKER.device_type_id, pattern_value="echo",
                            evidence_type=MDNS, resulting_device_name="Echo Dot", resulting_model="3rd gen")
ECHO_SSDP = DeviceSignature(signature_id=11, device_type_id=SPEAKER.device_type_id, pattern_value="echo",
                            evidence_type=SSDP)
ROKU_SSDP = DeviceSignature(signature_id=12, device_type_id=TV.device_type_id, pattern_value="roku",
                            evidence_type=SSDP, resulting_device_name="Roku TV")

KNOWN_OUI = DiscoveredHost("10.0.0.5", "70:13:01:65:8E:F7")      # Vantiva
UNKNOWN_OUI = DiscoveredHost("10.0.0.6", "00:1B:44:11:3A:B7")    # not in the lookup
OUI_ITEM = EvidenceItem(OUI, "70:13:01", None, weight_for(OUI))


def rules(*signatures: DeviceSignature) -> IdentificationRules:
    """Active rules as 3.2.1 gives them: a retired signature is simply not in the list."""
    return IdentificationRules.from_reference(ReferenceRules(
        {VANTIVA.vendor_id: VANTIVA}, {"70:13:01": VANTIVA.vendor_id},
        {SPEAKER.device_type_id: SPEAKER, TV.device_type_id: TV}, signatures))


def stored(signature: DeviceSignature, value: str, weight: int | None = None) -> EvidenceItem:
    """Evidence an earlier scan saved for this signature."""
    return EvidenceItem(signature.evidence_type, value, signature.signature_id,
                        weight if weight is not None else weight_for(signature.evidence_type))


def run(host: DiscoveredHost = KNOWN_OUI, collected: list[RawEvidence] | None = None,
        stored_evidence: list[EvidenceItem] | None = None, active: tuple[DeviceSignature, ...] | None = None,
        trusted: bool = False, is_new_device: bool = True) -> IdentificationResult:
    signatures = active if active is not None else (ECHO_MDNS, ECHO_SSDP, ROKU_SSDP)
    return identify(host, collected or [], stored_evidence or [], rules(*signatures), trusted, is_new_device)


class IdentifyTests(unittest.TestCase):
    def test_oui_only_is_partial_score_20_and_flagged(self) -> None:
        result = run()
        found = result.identification
        self.assertEqual(found.level, IdentificationLevel.PARTIAL)
        self.assertEqual(found.breakdown.score, 20)
        self.assertEqual((found.vendor_id, found.device_type_id), (VANTIVA.vendor_id, UNKNOWN_TYPE_ID))
        self.assertIsNone(found.device_name)
        self.assertTrue(result.flagged)
        self.assertEqual(result.new_evidence, (OUI_ITEM,))

    def test_oui_and_a_strong_signature_is_identified_and_not_flagged(self) -> None:
        result = run(collected=[RawEvidence(MDNS, "Kitchen-Echo")])
        found = result.identification
        self.assertEqual(found.level, IdentificationLevel.IDENTIFIED)
        self.assertEqual(found.breakdown.score, 55)                      # OUI 20 + mDNS 35
        self.assertEqual((found.device_type_id, found.device_name, found.model),
                         (SPEAKER.device_type_id, "Echo Dot", "3rd gen"))
        self.assertFalse(result.flagged)
        self.assertEqual(result.new_evidence, (OUI_ITEM, stored(ECHO_MDNS, "Kitchen-Echo")))

    def test_nothing_known_is_unidentified_with_no_evidence(self) -> None:
        result = run(host=UNKNOWN_OUI)
        found = result.identification
        self.assertEqual(found.level, IdentificationLevel.UNIDENTIFIED)
        self.assertEqual((found.breakdown.score, found.vendor_id), (0, UNKNOWN_VENDOR_ID))
        self.assertTrue(result.flagged)
        self.assertEqual(result.new_evidence, ())


class StoredEvidenceTests(unittest.TestCase):
    def test_stored_evidence_counts_with_new_evidence(self) -> None:
        result = run(collected=[RawEvidence(SSDP, "Echo Show 8")],
                     stored_evidence=[OUI_ITEM, stored(ECHO_MDNS, "kitchen-echo")])
        found = result.identification
        self.assertEqual(found.breakdown.score, 90)                      # OUI 20 + mDNS 35 + SSDP 35
        self.assertEqual(found.level, IdentificationLevel.IDENTIFIED)
        self.assertEqual(found.device_name, "Echo Dot")                  # the stored match gives the name
        # only what this scan found is new; stored evidence is never saved again
        self.assertEqual(result.new_evidence, (OUI_ITEM, stored(ECHO_SSDP, "Echo Show 8")))

    def test_the_same_evidence_stored_and_found_again_counts_once(self) -> None:
        result = run(collected=[RawEvidence(MDNS, "Kitchen-Echo")],
                     stored_evidence=[stored(ECHO_MDNS, "kitchen-echo")])
        mdns = [c for c in result.identification.breakdown.contributions if c.evidence_type is MDNS]
        self.assertEqual(len(mdns), 1)
        self.assertEqual([item.raw_value for item in mdns[0].evidence], ["kitchen-echo"])   # stored spelling kept
        self.assertEqual(result.identification.breakdown.score, 55)

    def test_stored_evidence_of_a_retired_signature_is_not_counted(self) -> None:
        result = run(stored_evidence=[stored(ECHO_MDNS, "kitchen-echo")], active=(ECHO_SSDP, ROKU_SSDP))
        found = result.identification
        self.assertEqual(found.breakdown.score, 20)                      # the OUI only
        self.assertEqual((found.level, found.device_type_id), (IdentificationLevel.PARTIAL, UNKNOWN_TYPE_ID))
        self.assertTrue(result.flagged)

    def test_stored_evidence_an_edited_signature_no_longer_matches_is_not_counted(self) -> None:
        edited = DeviceSignature(signature_id=ECHO_MDNS.signature_id, device_type_id=SPEAKER.device_type_id,
                                 pattern_value="alexa", evidence_type=MDNS, resulting_device_name="Echo Dot")
        result = run(stored_evidence=[stored(ECHO_MDNS, "kitchen-echo")], active=(edited,))
        self.assertEqual(result.identification.breakdown.score, 20)

    def test_stored_evidence_counts_at_todays_weight(self) -> None:
        result = run(stored_evidence=[stored(ECHO_MDNS, "kitchen-echo", weight=10)])
        self.assertEqual(result.identification.breakdown.score, 55)      # 20 + 35, not 20 + 10

    def test_a_tie_leaves_the_type_unknown_but_saves_both_sides(self) -> None:
        result = run(collected=[RawEvidence(SSDP, "Roku Ultra")],
                     stored_evidence=[stored(ECHO_MDNS, "kitchen-echo")])
        found = result.identification
        self.assertEqual((found.level, found.device_type_id), (IdentificationLevel.PARTIAL, UNKNOWN_TYPE_ID))
        self.assertTrue(result.flagged)
        self.assertIn(stored(ROKU_SSDP, "Roku Ultra"), result.new_evidence)   # dropped, still saved for display


class TrustTests(unittest.TestCase):
    def test_a_trusted_known_device_stays_unflagged(self) -> None:
        result = run(trusted=True, is_new_device=False)
        self.assertEqual(result.identification.level, IdentificationLevel.PARTIAL)
        self.assertFalse(result.flagged)

    def test_the_same_device_under_a_new_mac_is_flagged(self) -> None:
        self.assertTrue(run(trusted=True, is_new_device=True).flagged)


if __name__ == "__main__":
    unittest.main()
