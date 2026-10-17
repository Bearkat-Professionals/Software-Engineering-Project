"""VENDOR: a manufacturer. Its OUI prefixes live in OUI_PREFIX (see oui_prefix.py)."""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.reference_entity import ReferenceEntity


@dataclass(kw_only=True)
class Vendor(ReferenceEntity):
    TABLE: ClassVar[str] = "vendor"
    PK: ClassVar[str] = "vendor_id"

    vendor_id: int | None = None
    vendor_name: str