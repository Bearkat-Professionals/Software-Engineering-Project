"""Scan and device API (#51): the endpoints the scan screen and the device list use.

    POST /api/scans            start a scan                  202, or 400 / 401 / 403 / 409
    GET  /api/scans/status     what the scanner is doing     200
    GET  /api/devices          one page of the device list   200, or 400 for bad paging

The JSON shapes are the contract with the screens (see "API" in the README): change
them only together with the screens. Every route checks access, makes ONE service call
and returns JSON. No database code here (tier rules): the services and stores do that.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from flask import Flask, abort, g, jsonify, request
from flask.typing import ResponseReturnValue

from backend.data_access.network_data import DeviceListing
from backend.data_access.network_data.device_store import DEFAULT_PER_PAGE, MAX_PER_PAGE
from backend.records import User
from backend.services.access_control.permissions import Permission, authorize
from backend.services.scan.scan_session import OpenRefusal, ScanState, ScanStatusReport

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

logger = logging.getLogger(__name__)
STATUS_URL = "/api/scans/status"
SCOPE_EXAMPLE = 'Send JSON like {"scope": "192.168.1.0/24"}.'
MAX_PAGE = 1_000_000            # keeps OFFSET far inside what MySQL accepts

# Why a scan wasn't started -> the HTTP status the screen gets
REFUSAL_STATUS: dict[OpenRefusal, int] = {
    OpenRefusal.INVALID_SCOPE: 400,     # Bad Request: fix the range
    OpenRefusal.UNKNOWN_USER: 401,      # Unauthorized: the account is gone, sign in again
    OpenRefusal.ALREADY_RUNNING: 409,   # Conflict: wait for the running scan (CM-15)
}


def register(app: Flask, services: Services) -> None:
    """Add the scan and device routes to the app (called by app_controller.register_routes)."""

    @app.post("/api/scans")
    def start_scan() -> ResponseReturnValue:
        user = _require(Permission.START_SCAN)
        body = request.get_json(silent=True)                 # None when it isn't JSON
        scope = body.get("scope") if isinstance(body, dict) else None
        if not isinstance(scope, str):
            abort(400, description=f"The scan range is missing. {SCOPE_EXAMPLE}")

        opened = services.scan_manager.start(scope, user.user_id)    # recorded as this user (CM-11)
        if not opened.opened:
            return jsonify(error=opened.reason), REFUSAL_STATUS[opened.refusal]
        return jsonify(scan_id=opened.scan.scan_id, status_url=STATUS_URL), 202

    @app.get("/api/scans/status")
    def scan_status() -> ResponseReturnValue:
        _require(Permission.VIEW_DATA)
        return jsonify(status_json(services.scan_sessions.status()))

    @app.get("/api/devices")
    def list_devices() -> ResponseReturnValue:
        _require(Permission.VIEW_DATA)
        page = _whole_number("page", default=1, largest=MAX_PAGE)
        per_page = _whole_number("per_page", default=DEFAULT_PER_PAGE, largest=MAX_PER_PAGE)
        found = services.devices.list_page(page=page, per_page=per_page)
        return jsonify(devices=[device_json(listing) for listing in found.devices],
                       page=found.page, per_page=found.per_page, total=found.total)


# ---- JSON shapes (the contract with the screens) ---------------------------------------

def status_json(report: ScanStatusReport) -> dict[str, Any]:
    """The scan status in one of its three shapes: running, finished, or no scans yet."""
    plain = report.to_dict()                                # enums and times already as text
    if report.state is ScanState.RUNNING:
        return {
            "running": True,
            "scan_id": plain["scan_id"],
            "scope": plain["scope"],
            "started_by": plain["started_by"],
            "started_at": plain["started_at"],
            "elapsed_seconds": plain["seconds_elapsed"],
            "phase": plain["phase"],
            "done": plain["done"],
            "total": plain["total"],
            "errors": plain["errors"],
        }
    if report.state is ScanState.FINISHED:
        return {"running": False, "last_scan": {
            "scan_id": plain["scan_id"],
            "scope": plain["scope"],
            "started_by": plain["started_by"],
            "started_at": plain["started_at"],
            "ended_at": plain["ended_at"],
            "outcome": plain["outcome"],
            "errors": plain["errors"],
        }}
    return {"running": False, "last_scan": None}


def device_json(listing: DeviceListing) -> dict[str, Any]:
    """One device for the list: vendor and type as NAMES, a blank name as null."""
    device = listing.device
    return {
        "device_id": device.device_id,
        "name": device.device_name or None,                 # the screen shows "Unknown"
        "type": listing.device_type_name,
        "vendor": listing.vendor_name,
        "ip": device.current_ip,
        "mac": device.mac_address,
        "is_randomized_mac": device.is_randomized_mac,
        "network_status": getattr(device.network_status, "value", device.network_status),
        "confidence": device.confidence_score,
        "flagged": device.flagged_status,
        "trusted": device.trusted_status,
        "last_seen": device.last_seen.isoformat() if device.last_seen is not None else None,
    }


# ---- helpers ---------------------------------------------------------------------------

def _require(permission: Permission) -> User:
    """The calling user, if they may do this; otherwise log it and stop with 401 or 403 as JSON.

    g.user is the #50 placeholder until M4 signs people in; this check stays as it is.
    Refusals are logged for the admin (AT-13): the username only, never the User (its repr has the hash)."""
    user: User | None = g.get("user")
    decision = authorize(user, permission)
    if not decision.allowed:
        who = user.username if user is not None else "nobody signed in"
        logger.warning("Refused %s %r for %s: needs %s (%s)",
                       request.method, request.path, who, permission.name, decision.reason)
        abort(401 if user is None else 403, description=decision.reason)
    return user


def _whole_number(name: str, *, default: int, largest: int) -> int:
    """A query value like ?page=2 as a number from 1 to largest; stops with 400 if it isn't."""
    text = request.args.get(name)
    if text is None:
        return default
    cleaned = text.strip()
    if not cleaned.isdecimal() or not 1 <= int(cleaned) <= largest:
        abort(400, description=f"{name} must be a whole number from 1 to {largest}, not '{text}'.")
    return int(cleaned)
