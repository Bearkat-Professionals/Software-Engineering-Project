"""AUDIT_LOG: who changed what record, old and new values, and when."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from backend.records.entity import Entity
from backend.records.enums import AuditAction


@dataclass(kw_only=True)
class AuditLog(Entity):
    TABLE: ClassVar[str] = "audit_log"
    PK: ClassVar[str] = "audit_log_id"
    ENUM_FIELDS: ClassVar[dict] = {"action": AuditAction}
    JSON_FIELDS: ClassVar[tuple] = ("old_value", "new_value")

    audit_log_id: int | None = None
    user_id: int                           # FK -> app_user (who)
    record_type: str                       # which table changed
    record_id: int                         # which row
    action: AuditAction
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    changed_at: datetime | None = None