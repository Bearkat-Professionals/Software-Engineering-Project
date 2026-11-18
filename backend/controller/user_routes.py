"""User-account administration routes (#92).

Only administrators have MANAGE_USERS. Every response uses User.to_dict(), so
password hashes never cross the controller boundary.
"""
from __future__ import annotations

from flask import Flask, g, jsonify
from flask.typing import ResponseReturnValue

from backend.controller.auth import requires
from backend.controller.route_helpers import json_body
from backend.services.access_control.access_control_service import (ACCOUNT_NOT_FOUND,
                                                                    AccountResult)
from backend.services.access_control.permissions import Permission


def register(app: Flask, services) -> None:
    @app.get("/api/users")
    @requires(Permission.MANAGE_USERS)
    def list_users() -> ResponseReturnValue:
        return jsonify(users=[user.to_dict() for user in services.access.list_users()])

    @app.post("/api/users")
    @requires(Permission.MANAGE_USERS)
    def create_user() -> ResponseReturnValue:
        body = json_body()
        result = services.access.create_user(
            body.get("username"), body.get("password"), body.get("role"),
            created_by=g.user.user_id)
        return _response(result, success=201)

    @app.put("/api/users/<int:user_id>/role")
    @requires(Permission.MANAGE_USERS)
    def set_role(user_id: int) -> ResponseReturnValue:
        result = services.access.set_role(
            user_id, json_body().get("role"), changed_by=g.user.user_id)
        return _response(result)

    @app.put("/api/users/<int:user_id>/status")
    @requires(Permission.MANAGE_USERS)
    def set_status(user_id: int) -> ResponseReturnValue:
        result = services.access.set_status(
            user_id, json_body().get("status"), changed_by=g.user.user_id)
        return _response(result)

    @app.put("/api/users/<int:user_id>/password")
    @requires(Permission.MANAGE_USERS)
    def reset_password(user_id: int) -> ResponseReturnValue:
        result = services.access.reset_password(
            user_id, json_body().get("password"), changed_by=g.user.user_id)
        return _response(result)

    @app.put("/api/me/password")
    @requires()
    def change_own_password() -> ResponseReturnValue:
        body = json_body()
        result = services.access.change_password(
            g.user, body.get("current_password"), body.get("new_password"))
        return _response(result)


def _response(result: AccountResult, *, success: int = 200) -> ResponseReturnValue:
    if result.ok:
        return jsonify(user=result.user.to_dict()), success
    status = 404 if result.reasons == (ACCOUNT_NOT_FOUND,) else 400
    return jsonify(error=" ".join(result.reasons), errors=list(result.reasons)), status
