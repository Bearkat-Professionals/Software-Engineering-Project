"""Application entry point (#50). Start it from the project root:

    py -m backend.main

The ONLY place the engine, stores and managers are created. Settings come from
.env, including SECRET_KEY and APP_PORT (see "Running the app" in the README).
Listens on 127.0.0.1 only, with debug mode off.
"""
import logging
import os

from backend.config import DatabaseConfig
from backend.controller.app_controller import Services, create_app, on_scan_finished
from backend.controller.auth import read_secret_key                                   
from backend.data_access.account_settings import UserStore
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine
from backend.data_access.network_data import DeviceStore, ScanStore
from backend.data_access.reference_data import OuiPrefixStore, ReferenceStore
from backend.records import DeviceSignature, DeviceType, Vendor
from backend.services.access_control.access_control_service import AccessControlService
from backend.services.records.reference_maintenance import ReferenceKind, ReferenceMaintenance                                    
from backend.services.scan.scan_manager import build_scan_manager
from backend.services.scan.scan_session import ScanSessionController
from backend.services.scan.scan_settings import ScanSettings

HOST = "127.0.0.1"
# The connection pool doesn't wait when it's empty: it fails at once. Flask runs each request
# on its own thread and a running scan holds a connection too, so leave room for a scan plus
# several requests at once (status polling, device list). Raise it if routes start failing.
DB_POOL_SIZE = 10

logger = logging.getLogger(__name__)


def read_port() -> int:
    text = (os.getenv("APP_PORT") or "5000").strip()
    if not text.isdecimal() or not 1 <= int(text) <= 65535:
        raise ValueError(f"APP_PORT in .env must be a whole number from 1 to 65535, not {text!r}.")
    return int(text)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        port = read_port()
        secret_key = read_secret_key()
        config = DatabaseConfig.from_env()
        settings = ScanSettings.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    try:
        engine = DatabaseEngine(config, pool_size=DB_POOL_SIZE)
    except DatabaseConnectionError as exc:
        raise SystemExit(f"FAILED: {exc}\nCheck: MAMP servers are running, the port matches MAMP's MySQL port, "
                         "the database exists in Workbench, and .env credentials are right.") from None

    users = UserStore(engine)
    sessions = ScanSessionController(ScanStore(engine), users)
    stuck = sessions.recover_stuck_scans()
    if stuck:
        logger.warning("Closed %d scan(s) left running by the last stop or crash", stuck)
    manager = build_scan_manager(engine, sessions, settings, on_finished=on_scan_finished)

    reference = ReferenceMaintenance(
        {ReferenceKind.VENDOR: ReferenceStore(engine, Vendor),
         ReferenceKind.DEVICE_TYPE: ReferenceStore(engine, DeviceType),
         ReferenceKind.SIGNATURE: ReferenceStore(engine, DeviceSignature)},
        OuiPrefixStore(engine), engine.unit_of_work)

    services = Services(scan_manager=manager, scan_sessions=sessions, devices=DeviceStore(engine),
                        users=users, access=AccessControlService(users), reference=reference)
    app = create_app(services, secret_key=secret_key)      # https=True once HTTPS arrives (M7)
    try:
        app.run(host=HOST, port=port, debug=False)
    finally:
        # The scan thread is a daemon and dies with the program, so close its row as
        # failed now instead of leaving it "running" until the next start.
        try:
            closed = sessions.recover_stuck_scans()
            if closed:
                logger.warning("Closed %d scan(s) still running at shutdown", closed)
        except Exception:
            logger.exception("Couldn't close running scans at shutdown; the next start will")


if __name__ == "__main__":
    main()
