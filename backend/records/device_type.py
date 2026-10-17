"""DEVICE_TYPE: category of device (smart speaker, phone, thermostat, ...)."""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.reference_entity import ReferenceEntity


@dataclass(kw_only=True)
class DeviceType(ReferenceEntity):
    TABLE: ClassVar[str] = "device_type"
    PK: ClassVar[str] = "device_type_id"

    device_type_id: int | None = None
    category_name: str                     # the table must include an "unknown" row (DI-03)