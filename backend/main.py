"""Application entry point (#50). Start it from the project root:

    py -m backend.main

The ONLY place the engine, stores and managers are created. Settings come from
.env, including DEV_USERNAME and APP_PORT (see "Running the app" in the README).
Listens on 127.0.0.1 only, with debug mode off.
"""
import logging
import os

from backend.config import DatabaseConfig
from backend.controller.app_controller import Services, create_app, on_scan_finished
from backend.data_access.account_settings import UserStore
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine
from backend.data_access.network_data import DeviceStore, ScanStore
from backend.records import User
from backend.services.scan.scan_manager import build_scan_manager
from backend.services.scan.scan_session import ScanSessionController
from backend.services.scan.scan_settings import ScanSettings

HOST = "127.0.0.1"

logger = logging.getLogger(__name__)


def read_port() -> int:
    text = (os.getenv("APP_PORT") or "5000").strip()
    if not text.isdecimal() or not 1 <= int(text) <= 65535:
        raise ValueError(f"APP_PORT in .env must be a whole number from 1 to 65535, not {text!r}.")
    return int(text)


def find_dev_user(users: UserStore) -> User:
    """PLACEHOLDER (M4): the user every request acts as until real login exists."""
    username = (os.getenv("DEV_USERNAME") or "").strip()
    if not username:
        raise SystemExit("DEV_USERNAME is not set. Add DEV_USERNAME=<an existing username> to .env.")
    user = users.find_by_username(username)
    if user is None:
        raise SystemExit(f"DEV_USERNAME is '{username}', but the database has no user with that name.")
    return user


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        port = read_port()
        config = DatabaseConfig.from_env()
        settings = ScanSettings.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    try:
        engine = DatabaseEngine(config)
    except DatabaseConnectionError as exc:
        raise SystemExit(f"FAILED: {exc}\nCheck: MAMP servers are running, the port matches MAMP's MySQL port, "
                         "the database exists in Workbench, and .env credentials are right.") from None

    users = UserStore(engine)
    dev_user = find_dev_user(users)
    sessions = ScanSessionController(ScanStore(engine), users)
    stuck = sessions.recover_stuck_scans()
    if stuck:
        logger.warning("Closed %d scan(s) left running by the last stop or crash", stuck)
    manager = build_scan_manager(engine, sessions, settings, on_finished=on_scan_finished)

    app = create_app(Services(manager, sessions, DeviceStore(engine), users), dev_user)
    logger.warning("NO LOGIN until M4: every request acts as '%s'", dev_user.username)
    app.run(host=HOST, port=port, debug=False)


if __name__ == "__main__":
    main()
