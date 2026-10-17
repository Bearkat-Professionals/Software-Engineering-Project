"""Base class for reference data (vendors, signatures, device types).

DI-06: reference records that devices use are retired, never deleted.
"""

from dataclasses import dataclass
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import RecordStatus

@dataclass
class ReferenceEntity(Entity):
    ENUM_FIELDS: ClassVar[dict] = {"status": RecordStatus}

    status: RecordStatus = RecordStatus.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.status == RecordStatus.ACTIVE

    def retire(self) -> None:
        self.status = RecordStatus.RETIRED