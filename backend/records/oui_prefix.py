"""OUI_PREFIX: one MAC-address prefix owned by a vendor.

A vendor can own many prefixes, so prefixes are rows of their own instead of a
column on VENDOR. Reference data only: it has no status column.
"""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.entity import Entity


@dataclass(kw_only=True)
class OuiPrefix(Entity):
    TABLE: ClassVar[str] = "oui_prefix"
    PK: ClassVar[str] = "oui_id"

    oui_id: int | None = None
    oui_prefix: str                        # unique (DI-07); text format to agree with the database
    vendor_id: int                         # FK -> vendor