"""Access control for the API (2.1 + 2.0, #90): sign in, sign out, "who am I", the session
cookie, and a permission check on every route (SEC-01, SEC-03, SEC-07, SEC-09, CP-01).

    @app.get("/api/devices")
    @requires(Permission.VIEW_DATA)     # 401 signed out, 403 wrong role, else g.user is set
    def list_devices(): ...

Every /api/ route has @requires(...) or @public (only POST /api/login); a test fails if
one has neither.

Sessions are kept on the server: the cookie holds only a random session id, signed with
SECRET_KEY. Signing out forgets the id, so an old copy of the cookie stops working. They
live in memory, so restarting the server signs everyone out.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any, Protocol

from flask import Flask, abort, current_app, g, jsonify, request, session
from flask.typing import ResponseReturnValue

from backend.records import User
from backend.records.enums import AccountStatus
from backend.services.access_control.permissions import Permission, authorize, permissions_for

if TYPE_CHECKING:
    from backend.controller.app_controller import Services

logger = logging.getLogger(__name__)

SECRET_KEY_MIN = 32
SESSION_MAX_S = 8* 60* 60
IDLE_MAX_S = 30 * 60
CHANGE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ACCESS_RULE = "access_rule"
EXTENSION = "katscan_access"

NOT_SIGNED_IN = "Sign in to continue."
SESSION_ENDED = "Your session has ended. Sign in again."
ACCOUNT_DISABLED = "This account is disabled."
DATABASE_DOWN = "The database is unavailable; see the server log"
NOT_JSON = "Send this request as JSON (Content-Type: application/json)."
LOGIN_EXAMPLE = 'Send JSON like {"username": "sam", "password": "..."}.'
MAKE_A_KEY = 'py -3.13 -c "import secrets; print(secrets.token_hex(32))"'

class UserFinder(Protocol):
    def find (self, user_id: int) -> User| None: ...

#settings 
def check_secret_key(key: str | None) -> str:
    """The key that signs session cookies. Refuses a missing or short one (a guessable key
    would let anyone make a cookie that says they're signed in)."""
    key = (key or "").strip()
    if len(key) < SECRET_KEY_MIN:
        raise ValueError(f"SECRET_KEY in .env must be at least {SECRET_KEY_MIN} characters. "
                         f"Make one with: {MAKE_A_KEY}")
    return key

def read_secret_key() -> str:
    return check_secret_key(os.getenv("SECRET_KEY"))

def install(app: Flask, services: Services, *, secret_key: str, https: bool = False,
            clock: Callable[[], float] = time.time) -> SessionRegistry:
    """Turn on sessions and the sign-in routes. Called once by create_app."""
    app.secret_key = check_secret_key(secret_key)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,          
        SESSION_COOKIE_SAMESITE="Strict",       
        SESSION_COOKIE_SECURE=https,           
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_MAX_S),
    )
    sessions = SessionRegistry(clock)
    app.extensions[EXTENSION] = _AccessState(sessions, services.users)

    @app.before_request
    def changes_must_be_json() -> None:
        """A form on another website can't send JSON, so with SameSite=Strict this stops
        it changing data with a signed-in user's cookie (CSRF)."""
        if request.method in CHANGE_METHODS and request.routing_exception is None and not request.is_json:
            abort(415, description=NOT_JSON)

    _register_routes(app, services, sessions)
    return sessions

#sessions
@dataclass
class _Session:
    user_id: int
    signed_in_at: float
    last_seen: float

    def ended(self, now: float) -> bool:
        return now - self.signed_in_at >= SESSION_MAX_S or now - self.last_seen >= IDLE_MAX_S

class SessionRegistry:
    """Signed-in sessions by id. Flask answers requests on several threads, so every
    read and change holds the lock."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def start(self, user_id: int) -> str:
        sid = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for old in [key for key, found in self._sessions.items() if found.ended(now)]:
                del self._sessions[old]             
            self._sessions[sid] = _Session(user_id, now, now)
        return sid

    def touch(self, sid: Any) -> int | None:
        """The user id of a live session, marked as used now; None once it has ended."""
        if not isinstance(sid, str):
            return None
        now = self._clock()
        with self._lock:
            found = self._sessions.get(sid)
            if found is None:
                return None
            if found.ended(now):
                del self._sessions[sid]
                return None
            found.last_seen = now
            return found.user_id

    def end(self, sid: Any) -> None:
        if isinstance(sid, str):
            with self._lock:
                self._sessions.pop(sid, None)

@dataclass(frozen=True)
class _AccessState:
    sessions: SessionRegistry
    users: UserFinder

#route protection
def public(view: Callable) -> Callable:
    """Anyone may call this route, signed in or not. Only POST /api/login uses it."""
    setattr(view, ACCESS_RULE, "public")
    return view

def requires(permission: Permission | None = None) -> Callable[[Callable], Callable]:
    """Only a signed-in, active user with this permission reaches the route (None: any
    signed-in user). The user is put on flask.g.user."""
    def decorate(view: Callable) -> Callable:
        @wraps(view)
        def guarded(*args: Any, **kwargs: Any) -> ResponseReturnValue:
            g.user = _check_access(permission)
            return view(*args, **kwargs)
        setattr(guarded, ACCESS_RULE, permission or "signed in")
        return guarded
    return decorate

def _check_access(permission: Permission | None) -> User:
    """The signed-in user, loaded fresh on every request; stops with 401 or 403 if not allowed."""
    state: _AccessState = current_app.extensions[EXTENSION]
    sid = session.get("sid")
    user_id = state.sessions.touch(sid)
    try:
        user = state.users.find(user_id) if user_id is not None else None
    except Exception:
        logger.exception("Couldn't load the signed-in user: the database is unavailable")
        abort(503, description=DATABASE_DOWN)

    if user is None:
        _end_session(state, sid)
        _refuse(401, NOT_SIGNED_IN if sid is None else SESSION_ENDED, None, permission)
    if user.account_status != AccountStatus.ACTIVE:         # disabled since signing in
        _end_session(state, sid)
        _refuse(401, ACCOUNT_DISABLED, user, permission)
    if permission is not None:
        decision = authorize(user, permission)
        if not decision.allowed:
            _refuse(403, decision.reason, user, permission)
    return user

def _end_session(state: _AccessState, sid: Any) -> None:
    state.sessions.end(sid)
    session.clear()             # the response tells the browser to drop the cookie

def _refuse(status: int, reason: str, user: User | None, permission: Permission | None) -> None:
    """Log it for the admin (AT-13) and stop. The username only, never the User (its repr
    has the hash) and never the request body (it may hold a password)."""
    who = user.username if user is not None else "nobody signed in"
    needs = permission.name if permission is not None else "sign-in"
    logger.warning("Refused %s %r for %s: needs %s (%s)", request.method, request.path, who, needs, reason)
    abort(status, description=reason)

#sign in, sign out, who am I
def _register_routes(app: Flask, services: Services, sessions: SessionRegistry) -> None:

    @app.post("/api/login")
    @public
    def login() -> ResponseReturnValue:
        body = request.get_json(silent=True)
        username = body.get("username") if isinstance(body, dict) else None
        password = body.get("password") if isinstance(body, dict) else None
        if not isinstance(username, str) or not isinstance(password, str):
            abort(400, description=f"The username or password is missing. {LOGIN_EXAMPLE}")

        result = services.access.authenticate(username, password)
        if not result.signed_in:
            return jsonify(error=result.reason), 401

        sessions.end(session.get("sid"))
        session.clear()             # never reuse a session made before sign-in (session fixation)
        session["sid"] = sessions.start(result.user.user_id)
        session.permanent = True    # the cookie lasts the 8 hours, not just until the browser closes
        return jsonify(_me(result.user))

    @app.post("/api/logout")
    @requires()
    def logout() -> ResponseReturnValue:
        sessions.end(session.get("sid"))
        session.clear()
        return "", 204

    @app.get("/api/me")
    @requires()
    def me() -> ResponseReturnValue:
        return jsonify(_me(g.user))

def _me(user: User) -> dict[str, Any]:
    """The user without the password hash (to_dict drops it), and what they may do."""
    return {"user": user.to_dict(), "permissions": permissions_for(user)}