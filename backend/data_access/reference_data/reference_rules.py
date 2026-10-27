"""3.2.1 Find Reference Rules: everything a scan matches against, loaded at once.

A scan loads this ONCE (design 14.1), then looks things up in memory instead
of querying the database for every host. Only ACTIVE records are included, so
retired vendors, device types and signatures produce no new matches (DI-06,
AT-08), while devices that already use them keep them.

    rules = find_reference_rules(engine)
    rules.vendor_for_prefix("28:6F:B9")      # -> Vendor, or None
    rules.prefixes_by_vendor[acme_id]        # -> ("28:6F:B9", "3C:07:54")
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.repository import Repository
from backend.records import DeviceSignature, DeviceType, OuiPrefix, Vendor
from backend.records.enums import RecordStatus


@dataclass(frozen=True)
class ReferenceRules:
    vendors: dict[int, Vendor]                  # vendor_id -> active vendor
    prefixes: dict[str, int]                    # "28:6F:B9" -> vendor_id (active vendors only)
    device_types: dict[int, DeviceType]         # device_type_id -> active type
    signatures: tuple[DeviceSignature, ...]     # active signatures whose type is also active
    prefixes_by_vendor: dict[int, tuple[str, ...]] = field(init=False)

    def __post_init__(self):
        grouped: dict[int, list[str]] = defaultdict(list)
        for prefix, vendor_id in sorted(self.prefixes.items()):
            grouped[vendor_id].append(prefix)
        # frozen dataclass: set the derived field this way, once, while building
        object.__setattr__(self, "prefixes_by_vendor",
                           {vendor_id: tuple(found) for vendor_id, found in grouped.items()})

    def vendor_for_prefix(self, prefix: str) -> Vendor | None:
        """The active vendor owning an "AA:BB:CC" prefix, or None."""
        vendor_id = self.prefixes.get(prefix.upper())
        return self.vendors.get(vendor_id) if vendor_id is not None else None


def find_reference_rules(engine: DatabaseEngine, unit: Any = None) -> ReferenceRules:
    """Load every active vendor (with its prefixes), device type and signature.

    All four reads share one transaction, so they see the reference data as it
    was at one moment, even if someone edits it while a scan is starting.
    """
    if unit is None:
        with engine.unit_of_work() as cursor:
            return _load(engine, cursor)
    return _load(engine, unit)


def _load(engine: DatabaseEngine, cursor: Any) -> ReferenceRules:
    active = RecordStatus.ACTIVE
    vendors = {vendor.pk: vendor for vendor in Repository(engine, Vendor).list(cursor, status=active)}
    device_types = {kind.pk: kind for kind in Repository(engine, DeviceType).list(cursor, status=active)}
    prefixes = {prefix.oui_prefix: prefix.vendor_id
                for prefix in Repository(engine, OuiPrefix).list(cursor)
                if prefix.vendor_id in vendors}                       # skip retired vendors' prefixes
    signatures = tuple(signature
                       for signature in Repository(engine, DeviceSignature).list(cursor, status=active)
                       if signature.device_type_id in device_types)   # skip signatures of retired types
    return ReferenceRules(vendors, prefixes, device_types, signatures)
