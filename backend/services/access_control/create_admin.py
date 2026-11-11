"""Create the first admin account on a fresh database (#89).

    .\\dev admin                                    the test database
    .\\dev admin --database network_device_scanner  the real one

Asks for the username, then the password twice without showing it (getpass). The
password is never a command-line argument, because those end up in the shell history.
Refuses, and changes nothing, once the database has any user: after that, admins add
accounts in the app so every account has an accountable creator (3.3.2).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable
from dataclasses import replace

from backend.black_boxes.password_hashing import hash_password
from backend.config import DatabaseConfig
from backend.data_access.account_settings import FirstUserExistsError, UserStore
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine
from backend.records import User
from backend.records.enums import UserRole
from backend.services.access_control.access_control_service import AccessControlService

TRIES = 3       # chances to type a valid username, or a matching valid password


def create_first_admin(users: UserStore, ask: Callable[[str], str] = input,
                       ask_secret: Callable[[str], str] = getpass.getpass) -> int:
    """The prompts and the save. Returns the exit code: 0 created, 1 refused."""
    rules = AccessControlService(users)

    username = _ask_until_valid(lambda: ask("Username: ").strip(), rules.check_username)
    if username is None:
        return 1

    def ask_password() -> str | None:
        password = ask_secret("Password (12 to 128 characters, not shown): ")
        if password != ask_secret("Same password again: "):
            print("The two passwords don't match.")
            return None
        return password

    password = _ask_until_valid(ask_password, lambda typed: rules.check_password_rule(username, typed))
    if password is None:
        return 1

    admin = User(username=username, password_hash=hash_password(password), role=UserRole.ADMIN)
    try:
        saved = users.create_first_user(admin)
    except FirstUserExistsError:
        print("This database already has users, so nothing was changed. "
              "Sign in as an admin and add accounts in the app instead.")
        return 1
    print(f"Created admin '{saved.username}' (user id {saved.user_id}).")
    return 0


def _ask_until_valid(ask: Callable[[], str | None], problem: Callable[[str], str | None]) -> str | None:
    """Ask up to TRIES times; print each problem. None when every try failed."""
    for _ in range(TRIES):
        answer = ask()
        if answer is None:
            continue
        reason = problem(answer)
        if reason is None:
            return answer
        print(reason)
    print("No account was created.")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the first admin account on a fresh database (#89).")
    parser.add_argument("--database", help="database name (default: the test database)")
    args = parser.parse_args(argv)

    config = DatabaseConfig.from_env()
    database = args.database or os.getenv("TEST_DB_NAME", f"{config.database}_test")
    try:
        engine = DatabaseEngine(replace(config, database=database), pool_size=1)
    except DatabaseConnectionError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Creating the first admin in '{database}'.")
    try:
        return create_first_admin(UserStore(engine))
    except (KeyboardInterrupt, EOFError):           # Ctrl+C, or input closed
        print("\nCancelled. No account was created.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
