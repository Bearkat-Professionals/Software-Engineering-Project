"""Application Controller (2.0, #50): the Flask app the Web UI talks to.

Gets everything it uses from the Services record, which backend/main.py builds,
so tests can build one from fakes.

    app = create_app(services, secret_key=read_secret_key())

Errors are always JSON {"error": "..."}. Unexpected ones say only "see the server
log": the details go to the log, never the response (SEC-08). Passwords and hashes
are never logged or returned (POL-03), so don't log a whole User: its repr has the hash.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from backend.controller import api_routes, auth, device_routes, hold_routes, reference_routes, user_routes
from backend.controller.auth import requires
from backend.data_access.account_settings import UserStore
from backend.data_access.network_data import DeviceStore
from backend.services.access_control.access_control_service import AccessControlService
from backend.services.access_control.permissions import Permission
from backend.services.scan.scan_manager import ScanManager, ScanResult
from backend.services.scan.scan_session import ScanSessionController
from backend.shared_modules.edit_lock_guard import EditLockGuard
from backend.services.records.reference_maintenance import ReferenceMaintenance
from backend.services.records.device_maintenance import DeviceMaintenance
from backend.services.reporting import DeviceDetailService

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR = "Something went wrong; see the server log"


@dataclass(frozen=True)
class Services:
    scan_manager: ScanManager
    scan_sessions: ScanSessionController
    devices: DeviceStore
    users: UserStore
    access: AccessControlService
    reference: ReferenceMaintenance | None = None     # None only in tests that don't use reference data
    device_maintenance: DeviceMaintenance | None = None   # None only in tests that don't change devices
    device_detail: DeviceDetailService | None = None      # None only in tests that don't read one device
    edit_holds: EditLockGuard = field(default_factory=EditLockGuard)   # SH-2; main.py shares one with the services


def create_app(services: Services, *, secret_key: str, https: bool = False,
               clock: Callable[[], float] = time.time) -> Flask:
    """https=True marks the session cookie Secure (HTTPS arrives in M7). clock is for tests."""
    app = Flask(__name__)
    app.json.sort_keys = False          # keep fields in the order they were written
    app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]   # refuse DNS-rebinding requests

    auth.install(app, services, secret_key=secret_key, https=https, clock=clock)   # sessions, sign-in (#90)

    @app.get("/api/health")
    @requires(Permission.VIEW_DATA)
    def health():
        try:
            services.users.find(g.user.user_id)         # any query shows the database answers
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
    """Add the API routes. Each group lives in its own module with a register(app, services)."""
    api_routes.register(app, services)          # scans and the device list (#51)
    reference_routes.register(app, services)    # vendors, device types, signatures, OUI prefixes (#126)
    device_routes.register(app, services)       # one device: read, add, edit, mark, delete; the pickers (2.3.1)
    user_routes.register(app, services)         # account administration and change-password (#92)
    hold_routes.register(app, services)         # edit holds: take, renew, release (SH-2, #141)


def on_scan_finished(result: ScanResult) -> None:
    """Called by the Scan Manager once the outcome is saved. Only logs: email comes in M6."""
    summary = result.summary
    saved = (f"{summary.created} created, {summary.updated} updated, {summary.skipped_no_mac} skipped without a MAC, "
             f"{len(summary.failures)} failed" if summary is not None else "nothing saved")
    logger.info("Scan %s finished: %s (%s, %d error(s))", result.scan_id, result.outcome.value, saved,
                len(result.errors))
    for event, count in Counter(alert.event for alert in result.alerts).items():
        logger.info("Scan %s would send alert '%s': %d event(s)", result.scan_id, event.value, count)
