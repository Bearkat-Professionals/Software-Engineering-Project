"""2.2.4.4 Assign Identification Level (#68, spec 7.2).

    identification = assign_level(resolution, breakdown, oui_vendor_id)

- Identified: a known type, a name from a winning signature, and a score >= IDENTIFIED_MIN_SCORE.
- Partially identified: the type or the vendor is known.
- Unidentified: neither.
stored_level() is the same rule for a device as stored, for saves that have no evidence
(a user's add or edit, 2.3.1), so scans and hand edits flag a device the same way.
Name and model come from the strongest winning match that gives a name (highest weight,
then lowest signature id), and are only kept when the device is identified. Pure.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from backend.records.enums import IdentificationLevel
from backend.services.scan.calculate_score import ScoreBreakdown
from backend.services.scan.evidence import IDENTIFIED_MIN_SCORE, UNKNOWN_TYPE_ID, UNKNOWN_VENDOR_ID
from backend.services.scan.match_signatures import SignatureMatch
from backend.services.scan.resolve_conflicts import Resolution


@dataclass(frozen=True)
class Identification:
    level: IdentificationLevel
    device_type_id: int                 # UNKNOWN_TYPE_ID when no type won
    vendor_id: int                      # UNKNOWN_VENDOR_ID when the OUI gave none
    device_name: str | None             # only when identified
    model: str | None                   # only when identified
    breakdown: ScoreBreakdown


def assign_level(resolution: Resolution, breakdown: ScoreBreakdown,
                 vendor_id: int | None) -> Identification:
    type_known = resolution.device_type_id not in (None, UNKNOWN_TYPE_ID)
    vendor_known = vendor_id not in (None, UNKNOWN_VENDOR_ID)
    name, model = pick_name_and_model(resolution.winning_matches)

    level = stored_level(resolution.device_type_id, vendor_id, name, breakdown.score)
    if level is not IdentificationLevel.IDENTIFIED:
        name = model = None

    return Identification(level,
                          resolution.device_type_id if type_known else UNKNOWN_TYPE_ID,
                          vendor_id if vendor_known else UNKNOWN_VENDOR_ID,
                          name, model, breakdown)

def stored_level(device_type_id: int | None, vendor_id: int | None, device_name: str | None,
                 confidence_score: int | None) -> IdentificationLevel:
    """The level from a device's own fields: the one rule assign_level uses too.

    A user typing a name and a type doesn't raise confidence_score (only evidence does),
    so a device under IDENTIFIED_MIN_SCORE stays partial however it is edited."""
    type_known = device_type_id not in (None, UNKNOWN_TYPE_ID)
    vendor_known = vendor_id not in (None, UNKNOWN_VENDOR_ID)
    named = bool((device_name or "").strip())
    if type_known and named and (confidence_score or 0) >= IDENTIFIED_MIN_SCORE:
        return IdentificationLevel.IDENTIFIED
    if type_known or vendor_known:
        return IdentificationLevel.PARTIAL
    return IdentificationLevel.UNIDENTIFIED


def pick_name_and_model(matches: Iterable[SignatureMatch]) -> tuple[str | None, str | None]:
    """Name and model from the strongest match that gives a name; (None, None) if none does."""
    named = [match for match in matches if (match.resulting_device_name or "").strip()]
    if not named:
        return None, None
    best = min(named, key=lambda match: (-match.evidence.weight, match.evidence.signature_id))
    return best.resulting_device_name.strip(), (best.resulting_model or "").strip() or None