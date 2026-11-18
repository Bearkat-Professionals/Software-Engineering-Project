"""Reference data API (2.3.2, #126): the endpoints the reference data screens use.

One set of routes for the three kinds (design 8.2); <kind> is vendors, device-types or signatures.

    GET  /api/reference/<kind>                        one page, searchable, retired ones included
    GET  /api/reference/<kind>/<id>                   one record, its usage, and loaded_at
    POST /api/reference/<kind>                        add                  201
    PUT  /api/reference/<kind>/<id>                   edit (sends loaded_at)
    POST /api/reference/<kind>/<id>/retire            retire (sends loaded_at)
    POST /api/reference/<kind>/<id>/reactivate        reactivate (sends loaded_at)
    POST /api/reference/vendors/<id>/prefixes         give a vendor an OUI prefix   201
    PUT  /api/reference/prefixes/<prefix>             move a prefix to another vendor (sends loaded_at)

The JSON shapes are the contract with the screens (see "API" in the README). Every route is
guarded by @requires, makes ONE service call (a change then reads the record back for its
reply) and returns JSON. No database code here (tier rules).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from flask import Flask, abort, g, jsonify, request
from flask.typing import ResponseReturnValue

from backend.controller.auth import requires
from backend.data_access.reference_data.reference_store import DEFAULT_PER_PAGE, MAX_PER_PAGE
from backend.controller.route_helpers import (MAX_PAGE, being_edited_json, conflict_json, json_body, read_loaded_at,
                                              whole_number)
from backend.records.reference_entity import ReferenceEntity
from backend.services.access_control.permissions import Permission
from backend.services.records.reference_maintenance import (MaintenanceResult, MaintenanceStatus,
                                                            ReferenceDetail, ReferenceKind)

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

requires_view = requires(Permission.VIEW_DATA)
requires_edit = requires(Permission.EDIT_REFERENCE_DATA)

KINDS: dict[str, ReferenceKind] = {
    "vendors": ReferenceKind.VENDOR,
    "device-types": ReferenceKind.DEVICE_TYPE,
    "signatures": ReferenceKind.SIGNATURE,
}

# The API's field names -> the service's. Anything else in a body is refused.
API_FIELDS: dict[ReferenceKind, dict[str, str]] = {
    ReferenceKind.VENDOR: {"name": "vendor_name"},
    ReferenceKind.DEVICE_TYPE: {"name": "category_name"},
    ReferenceKind.SIGNATURE: {"pattern": "pattern_value", "evidence_type": "evidence_type",
                              "device_type_id": "device_type_id",
                              "resulting_device_name": "resulting_device_name",
                              "resulting_model": "resulting_model"},
}

# Why a change wasn't made -> the HTTP status the screen gets
REFUSAL_STATUS: dict[MaintenanceStatus, int] = {
    MaintenanceStatus.REJECTED: 400,      # Bad Request: fix the values (every reason listed)
    MaintenanceStatus.PROTECTED: 403,     # Forbidden: the built-in "unknown" rows
    MaintenanceStatus.NOT_FOUND: 404,
    MaintenanceStatus.DUPLICATE: 409,     # Conflict: that name, pattern or prefix exists
    MaintenanceStatus.CONFLICT: 409,      # Conflict: someone else changed it after it was loaded
    MaintenanceStatus.BEING_EDITED: 409,  # Conflict: someone else has it open (SH-2, retire only)
}

STATUSES = ("active", "retired", "both")
SEARCH_MAX = 100
RELOAD = "loaded_at is missing or unreadable. Reload the record and try again."


def register(app: Flask, services: Services) -> None:
    """Add the reference data routes to the app (called by app_controller.register_routes)."""

    @app.get("/api/reference/<kind>")
    @requires_view
    def list_records(kind: str) -> ResponseReturnValue:
        reference_kind = _kind(kind)
        page = whole_number("page", default=1, largest=MAX_PAGE)
        per_page = whole_number("per_page", default=DEFAULT_PER_PAGE, largest=MAX_PER_PAGE)
        listing = services.reference.list_page(reference_kind, page, per_page,
                                               text=_search_text(), status=_status())
        records = [record_json(reference_kind, record,
                               listing.device_type_names.get(getattr(record, "device_type_id", None)))
                   for record in listing.records]
        return jsonify(records=records, page=listing.page, per_page=listing.per_page, total=listing.total)

    @app.get("/api/reference/<kind>/<int:record_id>")
    @requires_view
    def get_record(kind: str, record_id: int) -> ResponseReturnValue:
        reference_kind = _kind(kind)
        detail = services.reference.load(reference_kind, record_id)
        if detail is None:
            abort(404, description=_missing(reference_kind, record_id))
        return jsonify(detail_json(reference_kind, detail))

    @app.post("/api/reference/<kind>")
    @requires_edit
    def add_record(kind: str) -> ResponseReturnValue:
        reference_kind = _kind(kind)
        body = json_body()
        values, reasons = _values(reference_kind, body, adding=True)
        if reasons:
            return _rejected(reference_kind.label, reasons)
        result = services.reference.add(reference_kind, values, g.user.user_id)
        return _record_reply(services, reference_kind, result, None, created=True)

    @app.put("/api/reference/<kind>/<int:record_id>")
    @requires_edit
    def edit_record(kind: str, record_id: int) -> ResponseReturnValue:
        reference_kind = _kind(kind)
        body = json_body()
        loaded_at = _loaded_at(body)
        values, reasons = _values(reference_kind, body, adding=False)
        if reasons:
            return _rejected(reference_kind.label, reasons)
        result = services.reference.edit(reference_kind, record_id, values, g.user.user_id, loaded_at)
        return _record_reply(services, reference_kind, result, loaded_at)

    @app.post("/api/reference/<kind>/<int:record_id>/retire")
    @requires_edit
    def retire_record(kind: str, record_id: int) -> ResponseReturnValue:
        reference_kind = _kind(kind)
        loaded_at = _loaded_at(json_body())
        result = services.reference.retire(reference_kind, record_id, g.user.user_id, loaded_at)
        return _record_reply(services, reference_kind, result, loaded_at)

    @app.post("/api/reference/<kind>/<int:record_id>/reactivate")
    @requires_edit
    def reactivate_record(kind: str, record_id: int) -> ResponseReturnValue:
        reference_kind = _kind(kind)
        loaded_at = _loaded_at(json_body())
        result = services.reference.reactivate(reference_kind, record_id, g.user.user_id, loaded_at)
        return _record_reply(services, reference_kind, result, loaded_at)

    @app.post("/api/reference/vendors/<int:vendor_id>/prefixes")
    @requires_edit
    def add_prefix(vendor_id: int) -> ResponseReturnValue:
        prefix = json_body().get("prefix")
        if not isinstance(prefix, str):
            abort(400, description='The prefix is missing. Send JSON like {"prefix": "28:6F:B9"}.')
        result = services.reference.add_prefix(vendor_id, prefix, g.user.user_id)
        return _prefix_reply(services, result, None, created=True)

    @app.put("/api/reference/prefixes/<prefix>")
    @requires_edit
    def move_prefix(prefix: str) -> ResponseReturnValue:
        body = json_body()
        loaded_at = _loaded_at(body)
        if "vendor_id" not in body:
            abort(400, description='Say which vendor gets the prefix: {"vendor_id": 12, "loaded_at": "..."}.')
        result = services.reference.move_prefix(prefix, body["vendor_id"], g.user.user_id, loaded_at)
        return _prefix_reply(services, result, loaded_at)

# ---- JSON shapes (the contract with the screens) ---------------------------------------

def record_json(kind: ReferenceKind, record: ReferenceEntity, device_type_name: str | None = None) -> dict[str, Any]:
    """One record as the lists and forms show it, with the API's field names."""
    status = getattr(record.status, "value", record.status)
    if kind is ReferenceKind.SIGNATURE:
        return {
            "id": record.pk,
            "evidence_type": getattr(record.evidence_type, "value", record.evidence_type),
            "pattern": record.pattern_value,
            "device_type_id": record.device_type_id,
            "device_type": device_type_name,
            "resulting_device_name": record.resulting_device_name,
            "resulting_model": record.resulting_model,
            "status": status,
        }
    return {"id": record.pk, "name": getattr(record, kind.name_field), "status": status}


def detail_json(kind: ReferenceKind, detail: ReferenceDetail) -> dict[str, Any]:
    """One record for the edit form: what uses it and the version the user is looking at."""
    body: dict[str, Any] = {
        "record": record_json(kind, detail.record, detail.device_type_name),
        "usage": dict(detail.usage),
        "usage_text": detail.usage_text,
    }
    if kind is ReferenceKind.VENDOR:
        body["prefixes"] = list(detail.prefixes)
    body["loaded_at"] = detail.loaded_at.isoformat()
    return body


# ---- replies ---------------------------------------------------------------------------

def _record_reply(services: Services, kind: ReferenceKind, result: MaintenanceResult,
                  loaded_at: datetime | None, *, created: bool = False) -> ResponseReturnValue:
    """Done: the record read back, in the same shape as GET. Otherwise the refusal."""
    if not result.done:
        return _refusal(services, kind.label, result, loaded_at)
    detail = services.reference.load(kind, result.record.pk)
    if detail is None:                                      # can't happen: nothing is ever deleted (DI-06)
        abort(404, description=_missing(kind, result.record.pk))
    return jsonify(detail_json(kind, detail)), 201 if created else 200


def _prefix_reply(services: Services, result: MaintenanceResult, loaded_at: datetime | None,
                  *, created: bool = False) -> ResponseReturnValue:
    if not result.done:
        return _refusal(services, "OUI prefix", result, loaded_at)
    return jsonify(prefix=result.record.oui_prefix, vendor_id=result.record.vendor_id,
                   loaded_at=result.changed_at.isoformat()), 201 if created else 200


def _refusal(services: Services, label: str, result: MaintenanceResult,
             loaded_at: datetime | None) -> ResponseReturnValue:
    status = REFUSAL_STATUS[result.status]
    if result.status is MaintenanceStatus.REJECTED:
        return _rejected(label, list(result.reasons))
    if result.status is MaintenanceStatus.CONFLICT:
        return _conflict(services, label, result, loaded_at), status
    if result.status is MaintenanceStatus.DUPLICATE:
        return jsonify(error=result.reasons[0], existing_id=result.existing_id), status
    if result.status is MaintenanceStatus.BEING_EDITED:
        return jsonify(being_edited_json(services.users, label, result.held_by, result.held_since)), status
    return jsonify(error=result.reasons[0]), status


def _rejected(label: str, reasons: list[str]) -> ResponseReturnValue:
    return jsonify(error=f"The {label} can't be saved.", reasons=reasons), 400

def _conflict(services: Services, label: str, result: MaintenanceResult,
              loaded_at: datetime | None) -> Any:
    """'This vendor was changed by sam at 14:05 after you opened it.' The service knows only the id."""
    return jsonify(conflict_json(services.users, label, result.changed_by, result.changed_at, loaded_at))


# ---- reading the request ---------------------------------------------------------------

def _kind(text: str) -> ReferenceKind:
    kind = KINDS.get(text)
    if kind is None:
        abort(404, description=f"There's no reference list called '{text[:40]}'. "
                               f"Use {', '.join(KINDS)}.")
    return kind

def _values(kind: ReferenceKind, body: dict[str, Any], *, adding: bool) -> tuple[dict[str, Any], list[str]]:
    """The body's fields under the service's names, and every problem with which fields were sent."""
    fields = API_FIELDS[kind]
    allowed = ", ".join(fields)
    reasons = [f"'{name[:40]}' can't be set on a {kind.label}. Send: {allowed}."
               for name in body if name not in fields and name != "loaded_at"]
    values = {fields[name]: value for name, value in body.items() if name in fields}
    if not adding and not values and not reasons:
        reasons.append(f"Nothing to change. Send at least one of: {allowed}.")
    return values, reasons


def _loaded_at(body: dict[str, Any]) -> datetime:
    """When the user loaded the record (from GET), as sent back. Required for every change to
    an existing record, so the conflict check always runs (CM-01)."""
    return read_loaded_at(body.get("loaded_at"), RELOAD)


def _search_text() -> str | None:
    text = (request.args.get("q") or "").strip()
    if len(text) > SEARCH_MAX:
        abort(400, description=f"The search text can be at most {SEARCH_MAX} characters.")
    return text or None


def _status() -> str | None:
    status = request.args.get("status")
    if status is None:
        return None
    if status not in STATUSES:
        abort(400, description=f"status must be {', '.join(STATUSES[:-1])} or {STATUSES[-1]}, not '{status[:20]}'.")
    return status


def _missing(kind: ReferenceKind, record_id: int) -> str:
    return f"There is no {kind.label} with id {record_id}. It may have been removed; reload the list."
