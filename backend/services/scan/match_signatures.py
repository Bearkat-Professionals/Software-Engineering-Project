"""Match collected evidence to device signatures (#65, spec 2.2.4.1).

The signature side of 2.2.4.1; OUI matching is match_oui. Weights come from
evidence.py (weight_for), never from here.

    rules = find_reference_rules(engine)                  # once per scan (design 14.1)
    matches = match_signatures(values, rules.signatures)  # values: RawEvidence from the collectors

Every match is returned, even when one value matches several signatures:
choosing between them is 2.2.4.2's job. Pure: nothing touches the database or
the network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.records import DeviceSignature
from backend.records.enums import EvidenceType
from backend.services.scan.evidence import EvidenceItem, RawEvidence, weight_for


@dataclass(frozen=True)
class SignatureMatch:
    evidence: EvidenceItem
    device_type_id: int
    resulting_device_name: str | None
    resulting_model: str | None


def match_signatures(values: Iterable[RawEvidence], signatures: Iterable[DeviceSignature]) -> list[SignatureMatch]:
    """Sorted by signature id, then value. A value collected twice matches once, even
    with different spaces or case around it ("Echo-Dot" / "echo-dot"), because matching
    ignores case too; the first spelling seen is the one kept as evidence."""
    seen: dict[tuple[EvidenceType, str], str] = {}
    for raw in values:
        value = (raw.raw_value or "").strip()
        seen.setdefault((raw.evidence_type, value.casefold()), value)   # first spelling is kept
    active = [signature for signature in signatures if signature.is_active]     # retired never match (DI-06)
    matches = [SignatureMatch(EvidenceItem(evidence_type, value, signature.signature_id, weight_for(evidence_type)),
                              signature.device_type_id, signature.resulting_device_name, signature.resulting_model)
               for (evidence_type, _), value in seen.items()
               for signature in active
               if signature.evidence_type == evidence_type and signature.matches(value)]
    return sorted(matches, key=lambda match: (match.evidence.signature_id, match.evidence.raw_value))
