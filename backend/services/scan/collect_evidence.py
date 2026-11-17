"""2.2.3 Evidence Collection Manager (#147).

For now only the shape every collector returns, so the collectors (2.2.3.1 to
2.2.3.3) can be built at the same time. Subtask 4 adds the manager that runs them
during a scan and merges their results.

    result = collect_ssdp(["192.168.1.1", "192.168.1.20"], wait_s=3.0)
    result.evidence["192.168.1.20"]     # -> [RawEvidence(EvidenceType.SSDP, "manufacturer=Roku; ...")]
    result.available                    # False only when the method couldn't run at all
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.services.scan.evidence import RawEvidence


@dataclass(frozen=True)
class CollectorResult:
    method: str                                     # "mdns", "ssdp", "netbios", "reverse_dns"
    evidence: dict[str, list[RawEvidence]]          # ip -> what was found
    hostnames: dict[str, str]                       # ip -> name, if this method finds names
    available: bool = True                          # False: the method couldn't run at all
    reason: str | None = None                       # why, for the scan's error list


Collector = Callable[[Sequence[str], float], CollectorResult]    # (in-scope host IPs, seconds allowed)
