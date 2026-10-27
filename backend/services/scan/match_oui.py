"""Match a host's MAC prefix (OUI) to a vendor (#38, spec 2.2.4.1).

OUI evidence only, and pure: the lookup is built from rows passed in, so
nothing here touches the database or the network.

    lookup = build_oui_lookup(vendors, prefixes)
    match = match_oui(host, lookup)     # OuiMatch, or None
"""
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

from backend.records import OuiPrefix, Vendor
from backend.records.enums import EvidenceType

# PROVISIONAL: the real weights come from spec 7.1 / design open item 7, set in M3
OUI_EVIDENCE_WEIGHT = 20

# Same shape the ARP box accepts: 70-13-01-65-8e-f7 or 70:13:01:65:8E:F7
MAC = re.compile(r"[0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5}")

OuiLookup = dict[str, tuple[int, str]]      # "28:6F:B9" -> (vendor_id, vendor_name)

# TODO: load_oui_lookup() (read vendors and prefixes from the database) waits for
# #16 (Reference data, 3.2.1 Find Reference Rules), which isn't merged yet.


class HostWithMac(Protocol):
    """Anything with these two attributes; DiscoveredHost (#23) will be one."""
    @property
    def mac_address(self) -> str | None: ...
    @property
    def is_randomized_mac(self) -> bool: ...


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


def build_oui_lookup(vendors: Iterable[Vendor], prefixes: Iterable[OuiPrefix]) -> OuiLookup:
    """Prefixes of retired vendors are left out (DI-06, AT-08)."""
    active = {vendor.vendor_id: vendor.vendor_name for vendor in vendors if vendor.is_active}
    return {prefix.oui_prefix.upper(): (prefix.vendor_id, active[prefix.vendor_id])
            for prefix in prefixes if prefix.vendor_id in active}


def match_oui(host: HostWithMac, lookup: OuiLookup) -> OuiMatch | None:
    """None for a randomized, missing, malformed or unknown MAC: no vendor and no evidence."""
    raw = (host.mac_address or "").strip()
    if host.is_randomized_mac or not MAC.fullmatch(raw):
        return None
    prefix = raw.replace("-", ":").upper()[:8]
    if is_locally_administered(prefix):
        return None
    found = lookup.get(prefix)
    if found is None:
        return None
    vendor_id, vendor_name = found
    return OuiMatch(vendor_id, vendor_name, EvidenceItem(
        evidence_type=EvidenceType.OUI, raw_value=prefix, signature_id=None,
        weight=OUI_EVIDENCE_WEIGHT))


def is_locally_administered(mac: str) -> bool:
    """Second hex digit 2, 6, A or E: the device made up the address, so it names no vendor (design 10.4)."""
    return mac[1] in "26AE"
