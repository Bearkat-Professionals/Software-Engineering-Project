"""Edit hold routes (SH-2, #141): an open edit form takes a hold, renews it every 30 seconds
and releases it when it closes. Meanwhile, another user's delete or retire of that record
is refused (409, naming who is editing).

    POST   /api/holds              {"record_type": "device", "record_id": 7}     201
    PUT    /api/holds/<hold_id>    {}                                            200, or 404 (ended)
    DELETE /api/holds/<hold_id>    {}                                            204

POST and PUT answer the same shape, so the form can show who else has the record open:

    {"hold_id": "k3J9...", "expires_in_s": 120,
     "others": [{"username": "sam", "since": "2026-11-13T14:05:12"}]}

Taking a hold needs the permission to change that record: EDIT_DEVICES for a device,
EDIT_REFERENCE_DATA for a vendor, device type or signature (otherwise a viewer could block
deletes). Renewing and releasing work only on your own holds. A hold id is a secret like a
session id: it is never logged. Holds live in memory (SH-2), so a restart ends them all.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, abort, g, jsonify
from flask.typing import ResponseReturnValue

from backend.controller.auth import check_permission, current_session_id, requires
from backend.controller.route_helpers import json_body
from backend.records import Device, DeviceSignature, DeviceType, Vendor
from backend.services.access_control.permissions import Permission
from backend.services.records.reference_maintenance import ReferenceKind
from backend.shared_modules.edit_lock_guard import Hold, RecordKey

if TYPE_CHECKING:       # for the type hint only: app_controller imports this module
    from backend.controller.app_controller import Services

# record_type (as audit_log.record_type names it) -> what you need to take a hold on one
PERMISSION_FOR: dict[str, Permission] = {
    Device.TABLE: Permission.EDIT_DEVICES,
    Vendor.TABLE: Permission.EDIT_REFERENCE_DATA,
    DeviceType.TABLE: Permission.EDIT_REFERENCE_DATA,
    DeviceSignature.TABLE: Permission.EDIT_REFERENCE_DATA,
}
REFERENCE_KIND_FOR: dict[str, ReferenceKind] = {kind.record_class.TABLE: kind for kind in ReferenceKind}
LARGEST_ID = 2_147_483_647                      # MySQL INT
BAD_TYPE = f"record_type must be one of {', '.join(PERMISSION_FOR)}."
BAD_ID = "record_id must be a whole number of 1 or more."
HOLD_ENDED = "This hold has ended (it expired, or you signed out). Take a new one."


def register(app: Flask, services: Services) -> None:
    """Add the hold routes to the app (called by app_controller.register_routes)."""

    @app.post("/api/holds")
    @requires()                                 # the permission depends on record_type: checked below
    def take_hold() -> ResponseReturnValue:
        key = _record_key(json_body())
        check_permission(g.user, PERMISSION_FOR[key[0]])
        if not _exists(services, key):
            abort(404, description=f"There is no {_label(key)} with id {key[1]}. It may have been "
                                   "removed; reload the list.")
        hold = services.edit_holds.take(key, g.user.user_id, current_session_id())
        return jsonify(_hold_json(services, hold)), 201

    @app.put("/api/holds/<hold_id>")
    @requires()
    def renew_hold(hold_id: str) -> ResponseReturnValue:
        hold = services.edit_holds.renew(hold_id, g.user.user_id)
        if hold is None:                        # expired, released, signed out, or someone else's
            abort(404, description=HOLD_ENDED)
        return jsonify(_hold_json(services, hold))

    @app.delete("/api/holds/<hold_id>")
    @requires()
    def release_hold(hold_id: str) -> ResponseReturnValue:
        services.edit_holds.release(hold_id, g.user.user_id)   # gone already: nothing to do
        return "", 204


def _record_key(body: dict[str, Any]) -> RecordKey:
    """("device", 7) from the request body; stops with 400 naming what's wrong."""
    record_type = body.get("record_type")
    record_id = body.get("record_id")
    if not isinstance(record_type, str) or record_type not in PERMISSION_FOR:
        abort(400, description=BAD_TYPE)
    if isinstance(record_id, bool) or not isinstance(record_id, int) or not 1 <= record_id <= LARGEST_ID:
        abort(400, description=BAD_ID)
    return record_type, record_id


def _exists(services: Services, key: RecordKey) -> bool:
    """Only an existing record can be held: a hold on a made-up id would block nothing,
    and taking one shouldn't tell anyone more than reading it would."""
    record_type, record_id = key
    if record_type == Device.TABLE:
        return services.device_maintenance.load(record_id).found
    return services.reference.load(REFERENCE_KIND_FOR[record_type], record_id) is not None


def _label(key: RecordKey) -> str:
    return "device" if key[0] == Device.TABLE else REFERENCE_KIND_FOR[key[0]].label


def _hold_json(services: Services, hold: Hold) -> dict[str, Any]:
    """The hold, and everyone else who has the same record open, oldest first."""
    others = services.edit_holds.held_by_others(hold.key, hold.user_id)
    return {"hold_id": hold.hold_id,
            "expires_in_s": int(services.edit_holds.lifetime_s),
            "others": [{"username": _username(services, other.user_id),
                        "since": other.since.isoformat(timespec="seconds")} for other in others]}


def _username(services: Services, user_id: int) -> str:
    user = services.users.find(user_id)
    return user.username if user is not None else "another user"
