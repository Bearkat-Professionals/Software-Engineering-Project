"""Application Controller (2.0, #50): the Flask app the Web UI talks to.

Gets everything it uses from the Services record, which backend/main.py builds,
so tests can build one from fakes.

    app = create_app(services, dev_user)

Errors are always JSON {"error": "..."}. Unexpected ones say only "see the server
log": the details go to the log, never the response (SEC-08). Passwords and hashes
are never logged or returned (POL-03), so don't log a whole User: its repr has the hash.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from backend.data_access.account_settings import UserStore
from backend.data_access.network_data import DeviceStore
from backend.records import User
from backend.services.scan.scan_manager import ScanManager, ScanResult
from backend.services.scan.scan_session import ScanSessionController

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = "Something went wrong; see the server log"


@dataclass(frozen=True)
class Services:
    scan_manager: ScanManager
    scan_sessions: ScanSessionController
    devices: DeviceStore
    users: UserStore


def create_app(services: Services, dev_user: User) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False          # keep fields in the order they were written
    app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]   # refuse DNS-rebinding requests

    @app.before_request
    def check_permissions() -> None:
        """Decide who is calling and store it for the route (flask.g.user).

        There is no login yet: every request acts as the development user, looked up
        once at start-up. 2.1 replaces this in M4 with a session lookup, and routes
        will then ask authorize() from access_control/permissions.py.
        """
        g.user = dev_user               # PLACEHOLDER (M4)

    @app.get("/api/health")
    def health():
        try:
            services.users.find(dev_user.user_id)       # any query shows the database answers
        except Exception:
            logger.exception("Health check: the database is unavailable")
            return jsonify(status="unavailable", database="unavailable"), 503
        return jsonify(status="ok", database="ok")

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        logger.exception("Unexpected error on %s %r", request.method, request.path)
        return jsonify(error=UNEXPECTED_ERROR), 500

    register_routes(app, services)
    return app


def register_routes(app: Flask, services: Services) -> None:
    """Empty for now: subtask 2 fills this in, so the routes live in their own module."""


def on_scan_finished(result: ScanResult) -> None:
    """Called by the Scan Manager once the outcome is saved. Only logs: email comes in M6."""
    summary = result.summary
    saved = (f"{summary.created} created, {summary.updated} updated, {summary.skipped_no_mac} skipped without a MAC, "
             f"{len(summary.failures)} failed" if summary is not None else "nothing saved")
    logger.info("Scan %s finished: %s (%s, %d error(s))", result.scan_id, result.outcome.value, saved,
                len(result.errors))
    for event, count in Counter(alert.event for alert in result.alerts).items():
        logger.info("Scan %s would send alert '%s': %d event(s)", result.scan_id, event.value, count)
