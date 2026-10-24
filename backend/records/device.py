"""DEVICE: one record per unique device found on (or added to) the network."""
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import NetworkStatus


@dataclass(kw_only=True)
class Device(Entity):
    TABLE: ClassVar[str] = "device"
    PK: ClassVar[str] = "device_id"
    ENUM_FIELDS: ClassVar[dict] = {"network_status": NetworkStatus}
    BOOL_FIELDS: ClassVar[tuple] = ("is_randomized_mac", "trusted_status", "flagged_status")
    # DEVICE.device_type_id and DEVICE.vendor_id are NOT NULL and defaults to the "unknown" type (DI-03),
    # so a device with no type yet is saved without that column.
    OMIT_IF_NONE: ClassVar[tuple] = ("device_type_id", "vendor_id")

    device_id: int | None = None
    mac_address: str                       # identity (DI-01), not the IP
    is_randomized_mac: bool = False        # DI-02: non-unique, never merge
    current_ip: str | None = None          # current or last known
    hostname: str | None = None
    device_type_id: int | None = None      # FK -> device_type
    vendor_id: int | None = None           # FK -> vendor ("unknown" vendor if no OUI match)
    device_name: str | None = None         # plain-English name
    model: str | None = None
    trusted_status: bool = False
    flagged_status: bool = False
    confidence_score: int = 0              # 0-100 (CHECK in the database)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    network_status: NetworkStatus = NetworkStatus.OFFLINE

    def record_sighting(self, current_ip: str, seen_at: datetime,
                        hostname: str | None = None) -> None:
        """Update this device when a scan sees it again (DI-01: update, never duplicate)."""
        self.current_ip = current_ip
        if hostname:
            self.hostname = hostname
        if self.first_seen is None:
            self.first_seen = seen_at
        self.last_seen = seen_at
        self.network_status = NetworkStatus.ONLINE