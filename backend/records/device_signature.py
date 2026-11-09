"""DEVICE_SIGNATURE: a rule used to recognize a specific kind of device."""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.enums import EvidenceType
from backend.records.reference_entity import ReferenceEntity


@dataclass(kw_only=True)
class DeviceSignature(ReferenceEntity):
    TABLE: ClassVar[str] = "device_signature"
    PK: ClassVar[str] = "signature_id"
    ENUM_FIELDS: ClassVar[dict] = {
        **ReferenceEntity.ENUM_FIELDS,
        "evidence_type": EvidenceType,
    }

    signature_id: int | None = None
    device_type_id: int                    # FK -> device_type
    pattern_value: str                     # value/pattern to match
    evidence_type: EvidenceType            # which kind of evidence it applies to
    resulting_device_name: str | None = None
    resulting_model: str | None = None

    def matches(self, value: str | None) -> bool:
        """True when pattern_value appears anywhere in value, ignoring case and the
        spaces around both. A blank (or None) pattern or value never matches.

        Plain text on purpose, NOT a regular expression: patterns are editable reference
        data, and a regex from user input can hang the server (ReDoS) or match far too much.
        """
        pattern = (self.pattern_value or "").strip().casefold()
        text = (value or "").strip().casefold()
        return bool(pattern) and pattern in text