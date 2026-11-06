"""Roles and permissions (#88, 2.1).

The ONE place this table is written, as agreed on #86. Everything else asks
authorize() or permissions_for().

    Permission            viewer  editor  admin
    --------------------  ------  ------  -----
    VIEW_DATA               yes     yes    yes
    EXPORT                  yes     yes    yes
    START_SCAN               -      yes    yes
    EDIT_DEVICES             -      yes    yes
    EDIT_REFERENCE_DATA      -      yes    yes
    MANAGE_ALERTS            -       -     yes
    BACKUP_RESTORE           -       -     yes
    MANAGE_USERS             -       -     yes
    VIEW_AUDIT_LOG           -       -     yes

    decision = authorize(user, Permission.START_SCAN)
    if not decision.allowed:
        print(decision.reason)      # "Your role (viewer) can't start scans."

Pure: no Flask, database or network.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from backend.records import User
from backend.records.enums import AccountStatus, UserRole


class Permission(Enum):
    VIEW_DATA = auto()
    EXPORT = auto()
    START_SCAN = auto()
    EDIT_DEVICES = auto()
    EDIT_REFERENCE_DATA = auto()
    MANAGE_ALERTS = auto()
    BACKUP_RESTORE = auto()
    MANAGE_USERS = auto()
    VIEW_AUDIT_LOG = auto()


_VIEWER = frozenset({Permission.VIEW_DATA, Permission.EXPORT})

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.VIEWER: _VIEWER,
    UserRole.EDITOR: _VIEWER | {Permission.START_SCAN, Permission.EDIT_DEVICES, Permission.EDIT_REFERENCE_DATA},
    UserRole.ADMIN: frozenset(Permission),
}

ACTION_PHRASES: dict[Permission, str] = {       # finishes "Your role (viewer) can't ..."
    Permission.VIEW_DATA: "view data",
    Permission.EXPORT: "export data",
    Permission.START_SCAN: "start scans",
    Permission.EDIT_DEVICES: "edit devices",
    Permission.EDIT_REFERENCE_DATA: "edit reference data",
    Permission.MANAGE_ALERTS: "manage alerts",
    Permission.BACKUP_RESTORE: "back up or restore data",
    Permission.MANAGE_USERS: "manage user accounts",
    Permission.VIEW_AUDIT_LOG: "view the audit log",
}


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None


def authorize(user: User | None, permission: Permission) -> AccessDecision:
    if user is None:
        return AccessDecision(False, "Sign in to continue.")
    if user.account_status != AccountStatus.ACTIVE:     # before the role: a disabled admin can do nothing
        return AccessDecision(False, "This account is disabled.")
    if permission not in ROLE_PERMISSIONS.get(user.role, frozenset()):      # deny by default
        return AccessDecision(False, f"Your role ({user.role.value}) can't {ACTION_PHRASES[permission]}.")
    return AccessDecision(True)


def permissions_for(user: User) -> list[str]:
    """The user's permission names, sorted, so the UI can hide what they can't use."""
    return sorted(permission.name for permission in Permission if authorize(user, permission).allowed)
