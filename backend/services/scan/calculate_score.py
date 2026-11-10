"""2.2.4.3 Calculate Confidence Score (spec 7.1, FR-10, OUT-02, DI-08).

Turns the evidence kept by conflict resolution (2.2.4.2) into a 0-100 score, with a
breakdown of which evidence earned which points, so every part of the score traces
to stored evidence (spec 7.1).

    resolution = resolve_conflicts(matches, [oui_item])
    breakdown = calculate_score(resolution.kept)
    breakdown.score            # 0 to 100
    breakdown.contributions    # one per evidence type: its points and the evidence behind them
    breakdown.capped           # True when the evidence was worth more than MAX_SCORE

Points come from count_points (evidence.py), the same function 2.2.4.2 uses, so the
score and conflict resolution can never disagree. Pure: no database, no network.
"""

from __future__ import annotations
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from backend.records.enums import EvidenceType
from backend.services.scan.evidence import MAX_SCORE, EvidenceItem, count_points

#Score Breakdown
@dataclass(frozen = True)
class Contribution:
    """What one evidence type added to the score, and the evidence behind it."""
    evidence_type: EvidenceType
    points: int
    evidence: tuple[EvidenceItem, ...]

@dataclass(frozen=True)
class ScoreBreakdown:
    score: int
    contributions: tuple[Contribution, ...]
    capped: bool

    @property
    def points(self) -> int:
        """The points before the cap: always the sum of the contributions."""
        return sum(contribution.points for contribution in self.contributions)


#Calculate Score
def calculate_score(kept: Iterable[EvidenceItem]) -> ScoreBreakdown:
    """Score the kept evidence: each evidence type once, capped at MAX_SCORE. Never raises."""
    by_type: dict[EvidenceType, list[EvidenceItem]] = defaultdict(list)
    for item in kept:
        by_type[item.evidence_type].append(item)

    contributions = tuple(
        Contribution(evidence_type, count_points(items), tuple(sorted(items, key=_item_key)))
        for evidence_type, items in sorted(by_type.items(), key=lambda pair: pair[0].value))

    points = sum(contribution.points for contribution in contributions)
    return ScoreBreakdown(score=min(points, MAX_SCORE), contributions=contributions, capped=points > MAX_SCORE)

def _item_key(item: EvidenceItem) -> tuple[int, str, int]:
    """A fixed order for the evidence inside one contribution (strongest first)."""
    signature_id = item.signature_id if item.signature_id is not None else -1
    return -item.weight, item.raw_value or "", signature_id
