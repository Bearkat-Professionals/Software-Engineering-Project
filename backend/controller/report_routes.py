"""Report and export API (#156, 2.0): the endpoints the change report and the export buttons use.

    GET /api/scans?page=1&per_page=20               the finished scans, newest first     VIEW_DATA
    GET /api/reports/changes?from=4&to=9            what changed between two scans       VIEW_DATA
                                                    (neither: the latest two)
    GET /api/export?scope=devices&<device filters>  a JSON file of the devices           EXPORT
    GET /api/export?scope=scan&scan_id=9            a JSON file of one scan              EXPORT

The JSON shapes are the contract with the screens (see "API" in the README, and the
samples in frontend/app/src/samples). Every route is guarded by @requires, makes ONE
service call and returns JSON; errors are {"error": "..."}. No database code here (tier rules).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from flask import Flask, Response, abort, g, jsonify, request
from flask.typing import ResponseReturnValue

from backend.controller.auth import requires
from backend.controller.route_helpers import MAX_PAGE, device_search_from_query, whole_number
from backend.data_access.network_data import FinishedScan
from backend.data_access.network_data.scan_store import DEFAULT_PER_PAGE, MAX_PER_PAGE
from backend.services.access_control.permissions import Permission
from backend.services.records.reference_maintenance import MaintenanceStatus
from backend.services.reporting.change_report import ChangeKind, ChangeReport, ComparedScan, ReportedDevice
from backend.services.reporting.export_json import export_text

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

LARGEST_ID = 2_147_483_647                  # an INT id
SCOPES = ("devices", "scan")
FILE_TIME = "%Y-%m-%dT%H%MZ"                # devices-2026-11-16T2005Z.json: no ":" (Windows file names)

# Why the service said no -> the HTTP status the screen gets
REFUSAL_STATUS: dict[MaintenanceStatus, int] = {
    MaintenanceStatus.REJECTED: 400,        # fix the request: only one scan chosen, too many devices, ...
    MaintenanceStatus.NOT_FOUND: 404,       # the scan doesn't exist or hasn't finished
}


def register(app: Flask, services: Services) -> None:
    """Add the report and export routes to the app (called by app_controller.register_routes)."""

    @app.get("/api/scans")
    @requires(Permission.VIEW_DATA)
    def list_scans() -> ResponseReturnValue:
        page = whole_number("page", default=1, largest=MAX_PAGE)
        per_page = whole_number("per_page", default=DEFAULT_PER_PAGE, largest=MAX_PER_PAGE)
        found = services.scans.finished(page, per_page)
        return jsonify(scans=[finished_scan_json(row) for row in found.scans],
                       page=found.page, per_page=found.per_page, total=found.total)

    @app.get("/api/reports/changes")
    @requires(Permission.VIEW_DATA)
    def change_report() -> ResponseReturnValue:
        # 0 can't be typed (whole_number wants 1 or more), so it stands for "not given"
        from_id = whole_number("from", default=0, largest=LARGEST_ID) or None
        to_id = whole_number("to", default=0, largest=LARGEST_ID) or None
        result = services.change_report.report(from_id, to_id)     # refuses only one of the two (400)
        _stop_unless_done(result.status, result.reasons)
        return jsonify(change_report_json(result.record))

    @app.get("/api/export")
    @requires(Permission.EXPORT)
    def export() -> ResponseReturnValue:
        scope = (request.args.get("scope") or "").strip()
        if scope not in SCOPES:
            abort(400, description=f"scope must be devices or scan, not '{scope[:20]}'.")
        if scope == "devices":
            result = services.export.export_devices(device_search_from_query(request.args), g.user)
        else:
            if not (request.args.get("scan_id") or "").strip():
                abort(400, description="scan_id is required with scope=scan, e.g. ?scope=scan&scan_id=9.")
            scan_id = whole_number("scan_id", default=0, largest=LARGEST_ID)
            result = services.export.export_scan(scan_id, g.user)
        _stop_unless_done(result.status, (result.reason,) if result.reason else ())
        return _download(result.document, scope)


# ---- JSON shapes (the contract with the screens) ---------------------------------------

def finished_scan_json(row: FinishedScan) -> dict[str, Any]:
    scan = row.scan
    return {
        "scan_id": scan.scan_id,
        "scope": scan.target_scope,
        "started_by": row.username,
        "started_at": _iso(scan.started_at),
        "ended_at": _iso(scan.ended_at),
        "outcome": getattr(scan.outcome, "value", scan.outcome),
        "devices_seen": row.observation_count,
    }


def change_report_json(report: ChangeReport) -> dict[str, Any]:
    return {
        "from_scan": _compared_json(report.from_scan),
        "to_scan": _compared_json(report.to_scan),
        "new": [_reported_json(device) for device in report.new],
        "missing": [_reported_json(device) for device in report.missing],
        "changed": [_reported_json(device) for device in report.changed],
        "outside_scope": report.outside_scope,
        "randomized": {kind.value: report.randomized_counts.get(kind, 0) for kind in ChangeKind},
        "partial_warning": report.partial_warning,
    }


def _compared_json(scan: ComparedScan) -> dict[str, Any]:
    return {"scan_id": scan.scan_id, "scope": scan.target_scope, "started_at": _iso(scan.started_at),
            "outcome": getattr(scan.outcome, "value", scan.outcome)}


def _reported_json(device: ReportedDevice) -> dict[str, Any]:
    """One device in the report: vendor and type as names, a blank name as null."""
    return {
        "device_id": device.device_id,
        "name": device.device_name or None,
        "type": device.device_type_name,
        "vendor": device.vendor_name,
        "mac": device.mac_address,
        "is_randomized_mac": device.is_randomized_mac,
        "old_ip": device.old_ip,
        "new_ip": device.new_ip,
        "old_hostname": device.old_hostname,
        "new_hostname": device.new_hostname,
        "first_seen_in_this_scan": device.first_seen_in_this_scan,
    }


# ---- replies ---------------------------------------------------------------------------

def _stop_unless_done(status: MaintenanceStatus, reasons: tuple[str, ...]) -> None:
    if status is not MaintenanceStatus.DONE:
        abort(REFUSAL_STATUS.get(status, 400), description=" ".join(reasons) or "The request can't be done.")


def _download(document: dict[str, Any], scope: str) -> Response:
    """The export as a file to save: named after its scope and the document's own UTC time."""
    exported_at = datetime.strptime(document["exported_at"], "%Y-%m-%dT%H:%M:%SZ")
    prefix = "devices" if scope == "devices" else f"scan-{document['scope']['scan_id']}"
    response = Response(export_text(document), content_type="application/json; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{prefix}-{exported_at:{FILE_TIME}}.json"'
    response.headers["Cache-Control"] = "no-store"          # the file lists the network: never cache it
    return response


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None
