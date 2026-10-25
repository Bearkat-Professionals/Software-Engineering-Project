"""The one result shape every BB-3 probe returns.

A probe never raises for network trouble. It always hands back a ProbeResult,
so one failing method or host can't stop the scan (SEC-14).
"""

from dataclasses import dataclass
from enum import Enum

class ProbeStatus(str, Enum):
    OK = "ok"                       # target answered
    NO_REPLY = "no_reply"           # the probe ran, nothing answered (unused address)
    TIMEOUT = "timeout"             # the probe itself hung and was stopped (SEC-15)
    UNAVAILABLE = "unavailable"     # the method could not run at all (no ping command, bad target)

@dataclass(frozen=True)
class ProbeResult:
    target: str                 # the address that was probed
    status: ProbeStatus
    elapsed_ms: int             # how long the probe took
    detail: str | None = None   # why, for TIMEOUT and UNAVAILABLE

    @property
    def responded(self) -> bool:
        """True only when the target actually answered"""
        return self.status == ProbeStatus.OK
