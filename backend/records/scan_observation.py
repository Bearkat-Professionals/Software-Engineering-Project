"""SCAN_OBSERVATION: what was seen for one device during one scan (basis of history)."""
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import NetworkStatus


@dataclass(kw_only=True)
class ScanObservation(Entity):
    TABLE: ClassVar[str] = "scan_observation"
    PK: ClassVar[str] = "observation_id"
    ENUM_FIELDS: ClassVar[dict] = {"status": NetworkStatus}

    observation_id: int | None = None
    scan_id: int                           # FK -> scan   (unique together with device_id)
    device_id: int                         # FK -> device
    ip_address: str | None = None
    hostname: str | None = None
    status: NetworkStatus = NetworkStatus.ONLINE
    observed_at: datetime | None = None