"""Scan and device API (#51): the endpoints the scan screen and the device list use.

    POST /api/scans            start a scan                  202, or 400 / 401 / 403 / 409
    GET  /api/scans/status     what the scanner is doing     200
    GET  /api/devices          one page of the device list,  200, or 400 for bad paging or search
                               filtered and sorted (2.4.1)

The JSON shapes are the contract with the screens (see "API" in the README): change
them only together with the screens. Every route is guarded by @requires (auth.py), makes
ONE service call and returns JSON. No database code here (tier rules): the services and stores do that.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, abort, g, jsonify, request
from flask.typing import ResponseReturnValue

from backend.controller.auth import requires
from backend.data_access.network_data import DeviceListing
from backend.data_access.network_data.device_store import DEFAULT_PER_PAGE, MAX_PER_PAGE
from backend.controller.route_helpers import MAX_PAGE, whole_number
from backend.records import User
from backend.services.access_control.permissions import Permission
from backend.services.reporting.search_devices import search_devices
from backend.services.scan.scan_session import OpenRefusal, ScanState, ScanStatusReport

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

STATUS_URL = "/api/scans/status"
SCOPE_EXAMPLE = 'Send JSON like {"scope": "192.168.1.0/24"}.'

# Why a scan wasn't started -> the HTTP status the screen gets
REFUSAL_STATUS: dict[OpenRefusal, int] = {
    OpenRefusal.INVALID_SCOPE: 400,     # Bad Request: fix the range
    OpenRefusal.UNKNOWN_USER: 401,      # Unauthorized: the account is gone, sign in again
    OpenRefusal.ALREADY_RUNNING: 409,   # Conflict: wait for the running scan (CM-15)
    OpenRefusal.SCAN_STARTING: 409,     # Conflict: another scan is starting, try again (CM-15)
}


def register(app: Flask, services: Services) -> None:
    """Add the scan and device routes to the app (called by app_controller.register_routes)."""

    @app.post("/api/scans")
    @requires(Permission.START_SCAN)
    def start_scan() -> ResponseReturnValue:
        user: User = g.user                                  # from the session, never the body (CM-11)
        body = request.get_json(silent=True)                 # None when it isn't JSON
        scope = body.get("scope") if isinstance(body, dict) else None
        if not isinstance(scope, str):
            abort(400, description=f"The scan range is missing. {SCOPE_EXAMPLE}")

        opened = services.scan_manager.start(scope, user.user_id)    # recorded as this user (CM-11)
        if not opened.opened:
            return jsonify(error=opened.reason), REFUSAL_STATUS[opened.refusal]
        return jsonify(scan_id=opened.scan.scan_id, status_url=STATUS_URL), 202

    @app.get("/api/scans/status")
    @requires(Permission.VIEW_DATA)
    def scan_status() -> ResponseReturnValue:
        return jsonify(status_json(services.scan_sessions.status()))

    @app.get("/api/devices")
    @requires(Permission.VIEW_DATA)
    def list_devices() -> ResponseReturnValue:
        page = whole_number("page", default=1, largest=MAX_PAGE)
        per_page = whole_number("per_page", default=DEFAULT_PER_PAGE, largest=MAX_PER_PAGE)
        # The service checks every search value; with none given it is #51's list exactly
        result = search_devices(services.devices, request.args, page=page, per_page=per_page)
        if not result.ok:
            return jsonify(error="The search can't be run.", reasons=list(result.reasons)), 400
        found = result.page
        gateway_mac = services.scans.latest_gateway_mac() if services.scans is not None else None
        return jsonify(devices=[device_json(listing, gateway_mac) for listing in found.devices],
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


def device_json(listing: DeviceListing, gateway_mac: str | None = None) -> dict[str, Any]:
    """One device for the list: vendor and type as NAMES, a blank name as null."""
    device = listing.device
    return {
        "device_id": device.device_id,
        "name": device.device_name or None,                 # the screen shows "Unknown"
        "type": listing.device_type_name,
        "vendor": listing.vendor_name,
        "ip": device.current_ip,
        "mac": device.mac_address,
        "is_gateway": _same_mac(device.mac_address, gateway_mac),
        "is_randomized_mac": device.is_randomized_mac,
        "network_status": getattr(device.network_status, "value", device.network_status),
        "confidence": device.confidence_score,
        "flagged": device.flagged_status,
        "trusted": device.trusted_status,
        "last_seen": device.last_seen.isoformat() if device.last_seen is not None else None,
    }


def _same_mac(mac: str | None, other: str | None) -> bool:
    return bool(mac and other and mac.casefold() == other.casefold())
