"""IDENTIFICATION_EVIDENCE: one piece of evidence behind a device's identification."""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import EvidenceType


@dataclass(kw_only=True)
class IdentificationEvidence(Entity):
    TABLE: ClassVar[str] = "identification_evidence"
    PK: ClassVar[str] = "evidence_id"
    ENUM_FIELDS: ClassVar[dict] = {"evidence_type": EvidenceType}

    evidence_id: int | None = None
    device_id: int                         # FK -> device
    signature_id: int | None = None        # FK -> device_signature (none for an OUI match)
    evidence_type: EvidenceType
    raw_value: str                         # exactly what was observed
    weight: int = 0                        # contribution to the confidence score (DI-04)