"""2.2.4.2 Resolve Conflicting Evidence (#66, spec 7.1).

When a device's signature matches point to different device types, the type with the
stronger evidence wins and only its evidence counts toward the score. "Stronger" is
count_points (evidence.py), the same function the score uses.

    resolution = resolve_conflicts(matches, [oui_item])
    resolution.device_type_id      # the winning type, or None
    resolution.kept                # evidence that counts toward the score
    resolution.dropped             # conflicting evidence, shown on the device detail page
    resolution.winning_matches     # the winner's matches, for the name and model (2.2.4.4)

Rules (agreed on #63):
- Evidence without a type (OUI) never conflicts and is always kept.
- A tie for strongest means no type wins: all signature evidence is dropped and the
  device stays "unknown" (and flagged). OUI evidence is still kept.
- The order the evidence comes in never changes the result.

Pure: filters data only, calls no other module (design 2.2.4.2). SignatureMatch is
imported only as a record type.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from backend.services.scan.evidence import EvidenceItem, count_points
from backend.services.scan.match_signatures import SignatureMatch


@dataclass(frozen=True)
class Resolution:
    device_type_id: int | None                  # None: no signature matched, or a tie
    kept: tuple[EvidenceItem, ...]              # counts toward the score (2.2.4.3)
    dropped: tuple[EvidenceItem, ...]           # left out of scoring, kept for display
    winning_matches: tuple[SignatureMatch, ...]  # the winning type's matches, for name and model


def resolve_conflicts(matches: Iterable[SignatureMatch],
                      other_evidence: Iterable[EvidenceItem]) -> Resolution:
    """Pick the device type the stronger evidence supports (spec 7.1). Never raises."""
    groups: dict[int, list[SignatureMatch]] = defaultdict(list)
    for match in matches:
        groups[match.device_type_id].append(match)

    strength = {type_id: count_points(match.evidence for match in group) for type_id, group in groups.items()}
    best = max(strength.values(), default=None)
    leaders = [type_id for type_id, points in strength.items() if points == best]
    winner = leaders[0] if len(leaders) == 1 else None          # a tie, or no matches: no type

    winning = groups[winner] if winner is not None else []
    kept = [*other_evidence, *(match.evidence for match in winning)]
    dropped = [match.evidence for type_id, group in groups.items() if type_id != winner for match in group]
    return Resolution(winner, _in_order(kept), _in_order(dropped),
                      tuple(sorted(winning, key=lambda match: _item_key(match.evidence))))


def _item_key(item: EvidenceItem) -> tuple[str, int, str, int]:
    """A fixed order for evidence, so shuffled input gives an identical result."""
    signature_id = item.signature_id if item.signature_id is not None else -1
    return item.evidence_type.value, signature_id, item.raw_value or "", item.weight


def _in_order(items: Iterable[EvidenceItem]) -> tuple[EvidenceItem, ...]:
    return tuple(sorted(items, key=_item_key))
