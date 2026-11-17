"""Device record API (2.3.1): the endpoints the device screens use to change devices, and
the two lists the edit form's pickers need.

    GET    /api/devices/<id>                    one device for its edit form and detail screen:
                                                loaded_at, inferred fields, score, conflicting evidence
    GET    /api/devices/<id>/history            where and when it was seen, newest first (2.4.2)
    POST   /api/devices                         add a device by hand            201
    PUT    /api/devices/<id>                    edit name, type, vendor, model (sends loaded_at)
    PUT    /api/devices/<id>/mark               trusted / flagged / automatic (sends loaded_at)
    DELETE /api/devices/<id>?loaded_at=...      delete, with its observations and evidence
    GET    /api/device-types                    active types, for the type picker
    GET    /api/vendors?search=app&limit=20     active vendors, for the vendor search box

The JSON shapes are the contract with the screens (see "API" in the README). Every route is
guarded by @requires, makes ONE service call (a change then reads the device back for its
reply; GET of one device asks Device Maintenance for loaded_at, then Device Detail) and
returns JSON. No database code here (tier rules).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from flask import Flask, abort, g, jsonify, request
from flask.typing import ResponseReturnValue

from backend.controller.api_routes import device_json
from backend.controller.auth import requires
from backend.controller.route_helpers import MAX_PAGE, conflict_json, json_body, read_loaded_at, whole_number
from backend.data_access.network_data import DeviceListing
from backend.data_access.reference_data.reference_store import SEARCH_MAX_LIMIT
from backend.records import Device, IdentificationEvidence
from backend.services.access_control.permissions import Permission
from backend.services.records.device_maintenance import (DeviceEdit, DeviceMaintenanceResult,
                                                         DeviceMaintenanceStatus, LoadedDevice)
from backend.services.reporting import DeviceDetail, DeviceHistoryEntry, EvidenceDetail
from backend.services.reporting.device_detail import DEFAULT_PER_PAGE as HISTORY_PER_PAGE
from backend.services.reporting.device_detail import MAX_PER_PAGE as HISTORY_MAX_PER_PAGE
from backend.services.scan.evidence import EvidenceItem

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

requires_view = requires(Permission.VIEW_DATA)
requires_edit = requires(Permission.EDIT_DEVICES)

# The fields each request may send, and the JSON type each must be
EDIT_FIELDS: dict[str, type] = {"name": str, "device_type_id": int, "vendor_id": int, "model": str}
ADD_FIELDS: dict[str, type] = {"mac": str, **EDIT_FIELDS, "ip": str, "trusted": bool}
MARK_FIELDS: dict[str, type] = {"mark": str}
TYPE_WORDS: dict[type, str] = {str: "text", int: "a whole number", bool: "true or false"}

# Why a change wasn't made -> the HTTP status the screen gets
REFUSAL_STATUS: dict[DeviceMaintenanceStatus, int] = {
    DeviceMaintenanceStatus.REJECTED: 400,          # fix the values (every reason listed)
    DeviceMaintenanceStatus.NOT_FOUND: 404,         # deleted, maybe by someone else (CM-12)
    DeviceMaintenanceStatus.CONFLICT: 409,          # changed by someone else since it was loaded
    DeviceMaintenanceStatus.ALREADY_EXISTS: 409,    # that MAC is already a device
}

RELOAD = "loaded_at is missing or unreadable. Reload the device and try again."
VENDOR_LIMIT = 20
SEARCH_MIN, SEARCH_MAX = 2, 100


def register(app: Flask, services: Services) -> None:
    """Add the device record routes to the app (called by app_controller.register_routes)."""

    @app.get("/api/devices/<int:device_id>")
    @requires_view
    def get_device(device_id: int) -> ResponseReturnValue:
        loaded = services.device_maintenance.load(device_id)        # first: loaded_at is read before the device
        if not loaded.found:
            abort(404, description=_missing(device_id))
        detail = services.device_detail.get_device_detail(device_id)
        if detail is None:                                          # deleted just now, by someone else
            abort(404, description=_missing(device_id))
        return jsonify(device=device_form_json(loaded), loaded_at=loaded.loaded_at.isoformat(),
                       **detail_json(detail))

    @app.get("/api/devices/<int:device_id>/history")
    @requires_view
    def device_history(device_id: int) -> ResponseReturnValue:
        page = whole_number("page", default=1, largest=MAX_PAGE)
        per_page = whole_number("per_page", default=HISTORY_PER_PAGE, largest=HISTORY_MAX_PER_PAGE)
        history = services.device_detail.get_device_history(device_id, page, per_page)
        if history is None:
            abort(404, description=_missing(device_id))
        return jsonify(observations=[observation_json(entry) for entry in history.entries],
                       page=history.page, per_page=history.per_page, total=history.total)

    @app.post("/api/devices")
    @requires_edit
    def add_device() -> ResponseReturnValue:
        body = json_body()
        reasons = _field_reasons(body, ADD_FIELDS, required=("mac",))
        if reasons:
            return _rejected(reasons)
        result = services.device_maintenance.add(body["mac"], _edit(body), g.user.user_id,
                                                 ip=body.get("ip"), trusted=body.get("trusted", False))
        return _device_reply(services, result, None, created=True)

    @app.put("/api/devices/<int:device_id>")
    @requires_edit
    def edit_device(device_id: int) -> ResponseReturnValue:
        body = json_body()
        loaded_at = read_loaded_at(body.get("loaded_at"), RELOAD)
        reasons = _field_reasons(body, EDIT_FIELDS)
        if reasons:
            return _rejected(reasons)
        result = services.device_maintenance.edit(device_id, _edit(body), g.user.user_id, loaded_at)
        return _device_reply(services, result, loaded_at)

    @app.put("/api/devices/<int:device_id>/mark")
    @requires_edit
    def mark_device(device_id: int) -> ResponseReturnValue:
        body = json_body()
        loaded_at = read_loaded_at(body.get("loaded_at"), RELOAD)
        reasons = _field_reasons(body, MARK_FIELDS, required=("mark",))
        if reasons:
            return _rejected(reasons)
        result = services.device_maintenance.set_mark(device_id, body["mark"], g.user.user_id, loaded_at)
        return _device_reply(services, result, loaded_at)

    @app.delete("/api/devices/<int:device_id>")
    @requires_edit
    def delete_device(device_id: int) -> ResponseReturnValue:
        loaded_at = read_loaded_at(request.args.get("loaded_at"), RELOAD)
        result = services.device_maintenance.delete(device_id, g.user.user_id, loaded_at)
        if not result.done:
            return _refusal(services, result, loaded_at)
        removed = result.removed or {}
        return jsonify(deleted=device_id, observations_removed=removed.get("observations_removed", 0),
                       evidence_removed=removed.get("evidence_removed", 0))

    @app.get("/api/device-types")
    @requires_view
    def list_device_types() -> ResponseReturnValue:
        types = services.reference.active_device_types()
        return jsonify(device_types=[{"device_type_id": device_type.pk, "name": device_type.category_name}
                                     for device_type in types])

    @app.get("/api/vendors")
    @requires_view
    def search_vendors() -> ResponseReturnValue:
        text = (request.args.get("search") or "").strip()
        if not SEARCH_MIN <= len(text) <= SEARCH_MAX:
            abort(400, description=f"Type {SEARCH_MIN} to {SEARCH_MAX} characters of the vendor's name to search.")
        limit = whole_number("limit", default=VENDOR_LIMIT, largest=SEARCH_MAX_LIMIT)
        vendors = services.reference.search_vendors(text, limit)
        return jsonify(vendors=[{"vendor_id": vendor.pk, "name": vendor.vendor_name} for vendor in vendors])


# ---- JSON shapes (the contract with the screens) ---------------------------------------

def mark_of(device: Device) -> str:
    """The user's choice, from the two stored flags (the reverse of DeviceMaintenance's MARKS)."""
    if device.trusted_status:
        return "trusted"
    if device.flag_set_by_user:
        return "flagged"
    return "automatic"


def device_form_json(loaded: LoadedDevice) -> dict[str, Any]:
    """The list's device fields (device_json) plus what the edit form needs."""
    device = loaded.device
    body = device_json(DeviceListing(device, loaded.vendor_name, loaded.device_type_name))
    body.update(
        device_type_id=device.device_type_id,
        vendor_id=device.vendor_id,
        model=device.model or None,
        hostname=device.hostname,
        mark=mark_of(device),
        first_seen=device.first_seen.isoformat() if device.first_seen is not None else None,
    )
    return body


def detail_json(detail: DeviceDetail) -> dict[str, Any]:
    """What the detail screen adds to the device: which fields the evidence inferred, how the
    score is made up, and the evidence that lost a conflict (2.4.2)."""
    kept = {EvidenceItem.from_record(item.evidence): item for item in detail.kept}
    breakdown = detail.breakdown
    return {
        "inferred": sorted(detail.inferred),
        "score": {
            "stored": detail.device.confidence_score,
            "calculated": breakdown.score,
            "capped": breakdown.capped,
            "outdated": detail.score_outdated,
            "contributions": [{"evidence_type": contribution.evidence_type.value,
                               "points": contribution.points,
                               "evidence": [_evidence_json(kept[item], with_type=False)
                                            for item in contribution.evidence]}
                              for contribution in breakdown.contributions],
        },
        "conflicting": [_evidence_json(item) for item in detail.dropped],
    }


def _evidence_json(detail: EvidenceDetail, *, with_type: bool = True) -> dict[str, Any]:
    """One piece of evidence. signature is null for evidence no signature matched (an OUI)."""
    evidence: IdentificationEvidence = detail.evidence
    body: dict[str, Any] = {"evidence_type": evidence.evidence_type.value} if with_type else {}
    body.update(
        raw_value=evidence.raw_value,
        signature=None if evidence.signature_id is None else {
            "signature_id": evidence.signature_id,
            "device_name": detail.resulting_device_name,
            "model": detail.resulting_model,
        },
        weight=evidence.weight,
    )
    return body


def observation_json(entry: DeviceHistoryEntry) -> dict[str, Any]:
    """One sighting for the history list."""
    observation = entry.observation
    return {
        "scan_id": entry.scan_id,
        "scan_started_at": entry.started_at.isoformat() if entry.started_at is not None else None,
        "observed_at": observation.observed_at.isoformat() if observation.observed_at is not None else None,
        "ip": observation.ip_address,
        "status": getattr(observation.status, "value", observation.status),
    }


# ---- replies ---------------------------------------------------------------------------

def _device_reply(services: Services, result: DeviceMaintenanceResult, loaded_at: datetime | None,
                  *, created: bool = False) -> ResponseReturnValue:
    """Done: the device read back, in the same shape as GET. Otherwise the refusal."""
    if not result.done:
        return _refusal(services, result, loaded_at)
    loaded = services.device_maintenance.load(result.device.pk)
    if not loaded.found:                                # deleted by someone else just now
        abort(404, description=_missing(result.device.pk))
    return jsonify(device=device_form_json(loaded), loaded_at=loaded.loaded_at.isoformat()), 201 if created else 200


def _refusal(services: Services, result: DeviceMaintenanceResult, loaded_at: datetime | None) -> ResponseReturnValue:
    status = REFUSAL_STATUS[result.status]
    if result.status is DeviceMaintenanceStatus.REJECTED:
        return _rejected(list(result.reasons))
    if result.status is DeviceMaintenanceStatus.CONFLICT:
        return jsonify(conflict_json(services.users, "device", result.changed_by, result.changed_at, loaded_at)), status
    if result.status is DeviceMaintenanceStatus.ALREADY_EXISTS:
        return jsonify(error=result.reasons[0], device_id=result.existing_device_id), status
    return jsonify(error=result.reasons[0]), status


def _rejected(reasons: list[str]) -> ResponseReturnValue:
    return jsonify(error="The device can't be saved.", reasons=reasons), 400


# ---- reading the request ---------------------------------------------------------------

def _field_reasons(body: Mapping[str, Any], fields: Mapping[str, type], *,
                   required: tuple[str, ...] = ()) -> list[str]:
    """Every problem with WHICH fields were sent and their JSON types (OUT-04). Their values
    (a valid MAC, an active vendor) are checked by the service. loaded_at is read separately."""
    allowed = ", ".join(fields)
    reasons: list[str] = []
    for name, value in body.items():
        if name == "loaded_at":
            continue
        kind = fields.get(name)
        if kind is None:
            reasons.append(f"'{name[:40]}' can't be sent here. Send: {allowed}.")
        elif not _is_a(value, kind):
            reasons.append(f"'{name}' must be {TYPE_WORDS[kind]}.")
    reasons += [f"'{name}' is required." for name in required if name not in body]
    return reasons


def _is_a(value: Any, kind: type) -> bool:
    if kind is int:                                     # True is an int in Python; not an id
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, kind)


def _edit(body: Mapping[str, Any]) -> DeviceEdit:
    """The editable fields; one left out stays None, which the service reads as "keep"."""
    return DeviceEdit(device_name=body.get("name"), device_type_id=body.get("device_type_id"),
                      vendor_id=body.get("vendor_id"), model=body.get("model"))


def _missing(device_id: int) -> str:
    return f"There is no device with id {device_id}. It may have been deleted; reload the list."
