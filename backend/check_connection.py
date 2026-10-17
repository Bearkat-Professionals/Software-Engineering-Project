"""Quick connectivity check. Run from the project root.

    python -m backend.check_connection
"""

from backend.config import DatabaseConfig
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine

def main() -> None:
    config = DatabaseConfig.from_env()
    print(f"Connecting to {config.host}:{config.port}, database '{config.database}' ...")

    try:
        engine = DatabaseEngine(config)
    except DatabaseConnectionError as exc:
        print(f"FAILED: {exc}")
        print("Check: MAMP servers are running, the port matches MAMP's MySQL port, "
              "the database exists in Workbench, and .env credientials are right.")
        return

    version = engine.fetch_one("SELECT VERSION() AS version")["version"]
    print(f"Connected: MySQL version: {version}")
    tables = engine.fetch_all("SHOW TABLES")
    if not tables:
        print("The database has no tables yet.")
    for row in tables:
        print(" -", list(row.values())[0])

if __name__ == "__main__":
    main()