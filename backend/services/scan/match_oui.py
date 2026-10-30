"""Match a host's MAC prefix (OUI) to a vendor (#38, spec 2.2.4.1).

OUI evidence only. Signature matching (mDNS, SSDP, ...) comes in M3.

    rules = find_reference_rules(engine)          # once per scan (design 14.1)
    lookup = oui_lookup_from_rules(rules)         # pure
    match = match_oui(host, lookup)               # OuiMatch, or None

load_oui_lookup(engine) does the first two steps in one call. Everything else
here is pure: nothing touches the database or the network.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from backend.data_access.database_engine import DatabaseEngine
from backend.data_access.reference_data import ReferenceRules, find_reference_rules
from backend.records import OuiPrefix, Vendor
from backend.records.enums import EvidenceType
from backend.services.scan.detect_randomized_mac import is_randomized_mac
from backend.services.scan.discovered_host import DiscoveredHost
from backend.shared_modules.validate_input import validate_mac

# PROVISIONAL: the real weights come from spec 7.1 / design open item 7, set in M3
OUI_EVIDENCE_WEIGHT = 20

OuiLookup = dict[str, tuple[int, str]]      # "28:6F:B9" -> (vendor_id, vendor_name)


@dataclass(frozen=True)
class EvidenceItem:
    """One identification_evidence row, minus device_id (added once the device is known)."""
    evidence_type: EvidenceType
    raw_value: str
    signature_id: int | None
    weight: int


@dataclass(frozen=True)
class OuiMatch:
    vendor_id: int
    vendor_name: str
    evidence: EvidenceItem


# ---- building the lookup ------------------------------------------------------

def load_oui_lookup(engine: DatabaseEngine, unit: Any = None) -> OuiLookup:
    """The one function with I/O: 3.2.1 Find Reference Rules, then build the lookup.

    A Scan Manager that already loaded the rules for this scan should call
    oui_lookup_from_rules(rules) instead, so the database is read only once.
    """
    return oui_lookup_from_rules(find_reference_rules(engine, unit))


def oui_lookup_from_rules(rules: ReferenceRules) -> OuiLookup:
    """Pure. The rules already hold only active vendors and their prefixes (DI-06, AT-08)."""
    return {prefix.upper(): (vendor_id, rules.vendors[vendor_id].vendor_name)
            for prefix, vendor_id in rules.prefixes.items() if vendor_id in rules.vendors}


def build_oui_lookup(vendors: Iterable[Vendor], prefixes: Iterable[OuiPrefix]) -> OuiLookup:
    """Pure, from plain records. Prefixes of retired vendors are left out (DI-06, AT-08)."""
    active = {vendor.vendor_id: vendor.vendor_name for vendor in vendors if vendor.is_active}
    return {prefix.oui_prefix.upper(): (prefix.vendor_id, active[prefix.vendor_id])
            for prefix in prefixes if prefix.vendor_id in active}


# ---- matching -----------------------------------------------------------------

def match_oui(host: DiscoveredHost, lookup: OuiLookup) -> OuiMatch | None:
    """None for a randomized, missing, malformed or unknown MAC: no vendor and no evidence.

    MACs are checked with the team's validate_mac, and "randomized" uses the same
    rule as 2.2.2.5 (is_randomized_mac), so there is one definition of each.
    """
    if host.is_randomized_mac or not host.mac_address:
        return None
    checked = validate_mac(host.mac_address)
    if not checked:
        return None
    mac = checked.value                          # normalized: "70:13:01:65:8E:F7"
    if is_randomized_mac(mac):                   # made-up address names no vendor (design 10.4)
        return None
    prefix = mac[:8]
    found = lookup.get(prefix)
    if found is None:
        return None
    vendor_id, vendor_name = found
    return OuiMatch(vendor_id, vendor_name, EvidenceItem(
        evidence_type=EvidenceType.OUI, raw_value=prefix, signature_id=None,
        weight=OUI_EVIDENCE_WEIGHT))
