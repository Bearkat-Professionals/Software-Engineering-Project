"""Evidence weights and the shared evidence record (#64, spec 7.1).

The ONE place for these numbers, as agreed in #63. Everything else imports them.

    Evidence type      Weight
    -----------------  ------
    OUI                    20
    mDNS / DNS-SD          35
    SSDP / UPnP            35
    NetBIOS / LLMNR        25
    reverse DNS            20

- Score: count_points of the kept evidence (each evidence type counted once, #63),
  capped at MAX_SCORE (100).
- Identified: a signature gave a type and a name AND score >= IDENTIFIED_MIN_SCORE (50).
  Partially identified: vendor or type known. Unidentified: neither.
- Tie rule: a tie between two types means the type stays unknown (and the device is flagged).

    item = match.evidence                          # an EvidenceItem from a matcher
    scans.add_evidence(item.to_record(device.pk), unit)
    items = [EvidenceItem.from_record(e) for e in scans.evidence_for(device.pk)]
"""
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass

from backend.records import IdentificationEvidence
from backend.records.enums import EvidenceType

EVIDENCE_WEIGHTS: dict[EvidenceType, int] = {
    EvidenceType.OUI: 20,
    EvidenceType.MDNS: 35,
    EvidenceType.SSDP: 35,
    EvidenceType.NETBIOS_LLMNR: 25,
    EvidenceType.REVERSE_DNS: 20,
}
MAX_SCORE = 100
IDENTIFIED_MIN_SCORE = 50


# the seeded "unknown" device type and vendor (DI-03); must match reference_store.UNKNOWN_ID
UNKNOWN_TYPE_ID = 1
UNKNOWN_VENDOR_ID = 1


def weight_for(evidence_type: EvidenceType) -> int:
    return EVIDENCE_WEIGHTS[evidence_type]


def count_points(items: Iterable[EvidenceItem]) -> int:
    """What a set of evidence is worth, before the MAX_SCORE cap: each evidence type
    counts ONCE, at its highest weight in the set (agreed on #63).

    Three mDNS services from one device are still one mDNS source, so they count 35,
    not 105. 2.2.4.2 measures "stronger" with this and 2.2.4.3 scores with it, so the
    two can never disagree.

        count_points([mdns_item, mdns_item_2, ssdp_item])    # -> 35 + 35 = 70
    """
    best: dict[EvidenceType, int] = {}
    for item in items:
        best[item.evidence_type] = max(item.weight, best.get(item.evidence_type, 0))
    return sum(best.values())


@dataclass(frozen=True)
class RawEvidence:
    """One value a collector saw, before matching (the M6 collectors return these)."""
    evidence_type: EvidenceType
    raw_value: str


@dataclass(frozen=True)
class EvidenceItem:
    """One identification_evidence row, minus device_id (added once the device is known)."""
    evidence_type: EvidenceType
    raw_value: str
    signature_id: int | None
    weight: int

    @classmethod
    def from_record(cls, evidence: IdentificationEvidence) -> EvidenceItem:
        """From a stored row, e.g. one from ScanStore.evidence_for."""
        return cls(evidence.evidence_type, evidence.raw_value, evidence.signature_id, evidence.weight)

    def to_record(self, device_id: int) -> IdentificationEvidence:
        """A row for ScanStore.add_evidence."""
        return IdentificationEvidence(device_id=device_id, evidence_type=self.evidence_type,
                                      raw_value=self.raw_value, signature_id=self.signature_id,
                                      weight=self.weight)
