"""Backup and Recovery API (2.5, #171): what the backup screen calls. Admin only.

    GET  /api/backups                    the backups, the last attempt, and what's running   200
    POST /api/backups                    take a full backup now                              201
    POST /api/backups/<name>/verify      check one backup                                     200
    POST /api/backups/<name>/restore     replace the database with it ({"confirm": "<name>"}) 200

A restore signs EVERYONE out: every other session (and every edit hold) ends just before
the database is replaced, and the admin's own session ends after it succeeds, so everyone
signs in again against the restored accounts. A refused restore (wrong confirmation, unknown
name, busy, a backup that fails verification) signs nobody out: the service calls on_start
only once the safety backup is taken and the restore really starts. If the restore itself
then fails, the others are already signed out, the database is unchanged, and the admin
stays signed in to read why.

Every route is guarded by @requires(BACKUP_RESTORE), makes ONE service call and returns
JSON. Replies never contain a file path: the service words its reasons without them.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flask import Flask, abort, g, jsonify
from flask.typing import ResponseReturnValue

from backend.black_boxes.backup_media import StoredBackup, is_safe_name
from backend.controller.auth import requires, sign_out_everyone_else, sign_out_here
from backend.controller.route_helpers import json_body
from backend.services.access_control.permissions import Permission
from backend.services.backup import BackupListing, BackupResult, BackupStatus, LastAttempt, Running
from backend.services.backup.backup_recovery_service import backup_kind

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

logger = logging.getLogger(__name__)

REFUSAL_STATUS: dict[BackupStatus, int] = {
    BackupStatus.REJECTED: 400,     # the confirmation didn't match, or the backup failed verification
    BackupStatus.NOT_FOUND: 404,
    BackupStatus.BUSY: 409,         # another backup or restore, or a scan, is running
    BackupStatus.FAILED: 500,       # it was tried and failed; nothing was changed
}


def register(app: Flask, services: Services) -> None:
    """Add the backup routes to the app (called by app_controller.register_routes)."""
    requires_backup = requires(Permission.BACKUP_RESTORE)

    @app.get("/api/backups")
    @requires_backup
    def list_backups() -> ResponseReturnValue:
        return jsonify(listing_json(services.backups.list_backups()))

    @app.post("/api/backups")
    @requires_backup
    def create_backup() -> ResponseReturnValue:
        result = services.backups.create_backup(g.user.user_id)
        if not result.ok:
            return _refusal(result)
        return jsonify(backup=backup_json(result.backup)), 201

    @app.post("/api/backups/<name>/verify")
    @requires_backup
    def verify_backup(name: str) -> ResponseReturnValue:
        _check_name(name)
        body = json_body()
        private_key, passphrase = _key_material(body)
        result = services.backups.verify(name, private_key_pem=private_key, passphrase=passphrase)
        if not result.ok:
            return _refusal(result)
        return jsonify(name=name, ok=not result.problems, problems=list(result.problems))

    @app.post("/api/backups/<name>/restore")
    @requires_backup
    def restore_backup(name: str) -> ResponseReturnValue:
        _check_name(name)
        body = json_body()
        confirm = body.get("confirm")
        private_key, passphrase = _key_material(body)
        result = services.backups.restore(name, confirm if isinstance(confirm, str) else "", g.user.user_id,
                                          private_key_pem=private_key, passphrase=passphrase,
                                          on_start=lambda: _sign_out_others(services))
        if not result.ok:
            return _refusal(result)
        sign_out_here(services)                 # last: the restored accounts may not even include this admin
        return jsonify(restored=name, safety_backup=result.safety_backup,
                       rows_restored=dict(result.rows_restored), signed_out=True)


# ---- JSON shapes (the contract with the backup screen) -------------------------------

def listing_json(listing: BackupListing) -> dict[str, Any]:
    return {"backups": [{"name": listed.name, "kind": listed.kind, "created_at": listed.created,
                         "size_bytes": listed.size_bytes, "encrypted": listed.encrypted}
                        for listed in listing.backups],
            "last_attempt": _attempt_json(listing.last_attempt),
            "busy": _running_json(listing.busy),
            "warning": listing.warning}


def backup_json(stored: StoredBackup) -> dict[str, Any]:
    """Only public metadata, never where the file or key is."""
    return {"name": stored.name, "kind": backup_kind(stored.name), "created_at": stored.modified_at,
            "size_bytes": stored.size_bytes, "encrypted": stored.encrypted}


def _attempt_json(attempt: LastAttempt | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    return {"action": attempt.action.value, "name": attempt.name, "by": attempt.by,
            "at": attempt.at.isoformat(timespec="seconds"), "status": attempt.status.value, "reason": attempt.reason}


def _running_json(running: Running | None) -> dict[str, Any] | None:
    if running is None:
        return None
    return {"action": running.action.value, "by": running.by, "since": running.since.isoformat(timespec="seconds")}


# ---- helpers -----------------------------------------------------------------------------

def _check_name(name: str) -> None:
    """A name BB-9 would refuse is answered like one that doesn't exist: 404."""
    if not is_safe_name(name):
        abort(404, description=f"There's no backup named '{name[:60]}'.")


def _key_material(body: dict[str, Any]) -> tuple[str | None, str | None]:
    """Read one-request key material without retaining or returning it."""
    private_key = body.get("private_key")
    passphrase = body.get("passphrase")
    return (private_key if isinstance(private_key, str) else None,
            passphrase if isinstance(passphrase, str) else None)


def _sign_out_others(services: Services) -> None:
    """The restore's on_start: just before the database is replaced."""
    ended = sign_out_everyone_else(services)
    logger.warning("restore by %s: signed out %d other session(s)", g.user.username, ended)


def _refusal(result: BackupResult) -> ResponseReturnValue:
    for event in result.alerts:                 # like on_scan_finished: email alerts aren't built yet
        logger.info("Backup by %s would send alert '%s'", g.user.username, event.value)
    body: dict[str, Any] = {"error": result.reasons[0] if result.reasons else "The backup request failed.",
                            "reasons": list(result.reasons)}
    if result.problems:
        body["problems"] = list(result.problems)
    return jsonify(body), REFUSAL_STATUS[result.status]
