"""Evidence weights and the shared evidence record (#64, spec 7.1).

The ONE place for these numbers, as agreed in #63. Everything else imports them.

    Evidence type      Weight
    -----------------  ------
    OUI                    20
    mDNS / DNS-SD          35
    SSDP / UPnP            35
    NetBIOS / LLMNR        25
    reverse DNS            20

- Score: the sum of the kept evidence weights, capped at MAX_SCORE (100).
- Identified: a signature gave a type and a name AND score >= IDENTIFIED_MIN_SCORE (50).
  Partially identified: vendor or type known. Unidentified: neither.
- Tie rule: a tie between two types means the type stays unknown (and the device is flagged).

    item = match.evidence                          # an EvidenceItem from a matcher
    scans.add_evidence(item.to_record(device.pk), unit)
    items = [EvidenceItem.from_record(e) for e in scans.evidence_for(device.pk)]
"""
from __future__ import annotations

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


def weight_for(evidence_type: EvidenceType) -> int:
    return EVIDENCE_WEIGHTS[evidence_type]


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
