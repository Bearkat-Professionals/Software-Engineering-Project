"""SCAN: one record per scan run."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from backend.records.entity import Entity
from backend.records.enums import ScanOutcome, ScanStatus


@dataclass(kw_only=True)
class Scan(Entity):
    TABLE: ClassVar[str] = "scan"
    PK: ClassVar[str] = "scan_id"
    ENUM_FIELDS: ClassVar[dict] = {"status": ScanStatus, "outcome": ScanOutcome}
    JSON_FIELDS: ClassVar[tuple] = ("configuration_used",)

    scan_id: int | None = None
    user_id: int                           # FK -> app_user: who started it (CM-11)
    target_scope: str                      # local subnet or user-specified range
    configuration_used: dict[str, Any] = field(default_factory=dict)  # filters, timeouts
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: ScanStatus = ScanStatus.RUNNING
    outcome: ScanOutcome | None = None     # empty until the scan ends
    error_message: str | None = None       # e.g. empty ARP cache, unreachable subnet

    @property
    def is_running(self) -> bool:
        return self.status == ScanStatus.RUNNING