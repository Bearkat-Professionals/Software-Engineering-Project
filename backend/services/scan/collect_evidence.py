"""2.2.3 Evidence Collection Manager (#147). For now only the shape every collector returns,
exactly as #147 writes it; the manager that runs the collectors comes in subtask 4.

    def collect_something(hosts: Sequence[str], wait_s: float) -> CollectorResult: ...
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.services.scan.evidence import RawEvidence


@dataclass(frozen=True)
class CollectorResult:
    method: str                                     
    evidence: dict[str, list[RawEvidence]]          
    hostnames: dict[str, str]                       
    available: bool = True                          
    reason: str | None = None                       


Collector = Callable[[Sequence[str], float], CollectorResult]    