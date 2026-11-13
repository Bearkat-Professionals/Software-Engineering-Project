"""2.2.4 Identification Manager (spec 7.1-7.3): identify one device from its evidence.

    id_rules = IdentificationRules.from_reference(find_reference_rules(engine))   # ONCE per scan
    result = identify(host, collected, stored_evidence, id_rules, trusted=False, is_new_device=True)
    result.identification       # level, type, vendor, name, model and score breakdown (2.2.4.4)
    result.flagged              # SH-4.1, spec 7.3
    result.new_evidence         # what this scan found, for 2.2.5.2 to save

Runs the leaves in order and passes each one's output to the next:
    2.2.4.1 match OUI and signatures -> 2.2.4.2 resolve conflicts -> 2.2.4.3 score
    -> 2.2.4.4 level -> SH-4.1 flag

The score comes from ALL the evidence for the device (spec 7.1): what this scan found
plus what earlier scans stored, each piece counted once. Stored evidence counts only
while its signature is active and still matches it (DI-06); stored OUI evidence is not
reused, because the OUI is matched again from the MAC on every scan.

Pure: no database, no network. The scan's save path (record_scan_results) loads the
stored evidence and saves the result.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from backend.data_access.reference_data import ReferenceRules
from backend.records import DeviceSignature
from backend.services.scan.assign_level import Identification, assign_level
from backend.services.scan.calculate_score import calculate_score
from backend.services.scan.discovered_host import DiscoveredHost
from backend.services.scan.evidence import EvidenceItem, RawEvidence, weight_for
from backend.services.scan.match_oui import OuiLookup, match_oui, oui_lookup_from_rules
from backend.services.scan.match_signatures import SignatureMatch, match_signatures
from backend.services.scan.resolve_conflicts import resolve_conflicts
from backend.shared_modules.evaluate_flag import evaluate_flag


@dataclass(frozen=True)
class IdentificationRules:
    """The reference rules in the shape identify() needs. Build it ONCE per scan
    (design 14.1): the OUI lookup is a pass over every prefix, too slow per host."""
    oui_lookup: OuiLookup
    signatures: tuple[DeviceSignature, ...]             # active only (3.2.1)
    signatures_by_id: dict[int, DeviceSignature]

    @classmethod
    def from_reference(cls, rules: ReferenceRules) -> IdentificationRules:
        return cls(oui_lookup_from_rules(rules), rules.signatures,
                   {signature.signature_id: signature for signature in rules.signatures})


@dataclass(frozen=True)
class IdentificationResult:
    identification: Identification
    flagged: bool
    new_evidence: tuple[EvidenceItem, ...]      # found by this scan; add_evidence skips what's stored


def identify(host: DiscoveredHost, collected: Iterable[RawEvidence], stored_evidence: Iterable[EvidenceItem],
             rules: IdentificationRules, trusted: bool, is_new_device: bool) -> IdentificationResult:
    """Identify one device. Never raises for odd evidence: what can't be matched is left out."""
    oui = match_oui(host, rules.oui_lookup)                                  # 2.2.4.1, OUI side
    oui_items = [oui.evidence] if oui is not None else []
    found_now = match_signatures(collected, rules.signatures)                # 2.2.4.1, signature side
    matches = _without_repeats([*_stored_matches(stored_evidence, rules.signatures_by_id), *found_now])

    resolution = resolve_conflicts(matches, oui_items)                       # 2.2.4.2
    breakdown = calculate_score(resolution.kept)                             # 2.2.4.3
    identification = assign_level(resolution, breakdown,                     # 2.2.4.4
                                  oui.vendor_id if oui is not None else None)
    flagged = evaluate_flag(identification.level, trusted, is_new_device)    # SH-4.1
    return IdentificationResult(identification, flagged,
                                (*oui_items, *(match.evidence for match in found_now)))


def _stored_matches(stored: Iterable[EvidenceItem],
                    signatures_by_id: dict[int, DeviceSignature]) -> list[SignatureMatch]:
    """Stored signature evidence turned back into matches, with the signature's CURRENT
    type, name and model, at today's weight. Left out: OUI evidence (matched again from
    the MAC), and evidence whose signature is retired or no longer matches it (DI-06)."""
    matches: list[SignatureMatch] = []
    for item in stored:
        signature = signatures_by_id.get(item.signature_id) if item.signature_id is not None else None
        if signature is None or signature.evidence_type != item.evidence_type \
                or not signature.matches(item.raw_value):
            continue
        matches.append(SignatureMatch(
            EvidenceItem(item.evidence_type, item.raw_value, item.signature_id, weight_for(item.evidence_type)),
            signature.device_type_id, signature.resulting_device_name, signature.resulting_model))
    return matches


def _without_repeats(matches: Iterable[SignatureMatch]) -> list[SignatureMatch]:
    """One match per (type, value ignoring case, signature); the first one seen is kept."""
    kept: dict[tuple[object, str, int | None], SignatureMatch] = {}
    for match in matches:
        item = match.evidence
        kept.setdefault((item.evidence_type, item.raw_value.strip().casefold(), item.signature_id), match)
    return list(kept.values())
