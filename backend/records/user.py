"""APP_USER: a person who uses the system.

The schema document calls this table both USER and APP_USER. APP_USER is used
here (USER is an awkward name in SQL); change TABLE if the database differs.
"""
from dataclasses import dataclass
from typing import ClassVar

from backend.records.entity import Entity
from backend.records.enums import AccountStatus, UserRole


@dataclass(kw_only=True)
class User(Entity):
    TABLE: ClassVar[str] = "app_user"
    PK: ClassVar[str] = "user_id"
    ENUM_FIELDS: ClassVar[dict] = {"role": UserRole, "account_status": AccountStatus}

    user_id: int | None = None
    username: str
    password_hash: str                     # hash only, never the password (SEC-02)
    role: UserRole = UserRole.VIEWER
    account_status: AccountStatus = AccountStatus.ACTIVE

    def to_dict(self) -> dict:
        """Credentials never leave the backend (POL-03, SEC-08)."""
        data = super().to_dict()
        data.pop("password_hash", None)
        return data