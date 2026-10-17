"""Backend configuration

Settings come from environment variables (or a .env file in the project root),
so each teammate can point at their own MAMP or LAMP MySQL without editing code.
"""

import os
from dataclasses import dataclass

try:    # python-dotenv is optional: env vars work without it
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

@dataclass(frozen=True)
class DatabaseConfig:
    user: str
    password: str               # Both user and password will need to be set and will not be hardcoded here
    host: str = "127.0.0.1"     # Use IP and not localhost so MySQL uses TCP
    port: int = 3306            # MAMP on Windows uses port 3306 and macOS uses 8889
    database: str = "network_device_scanner"
    

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        # This pulls values from the .env file first
        user = os.getenv("DB_USER")
        password = os.getenv("DB_PASSWORD")

        if not user or not password:
            # Fail safely and loudly
            raise ValueError("Database credentials (DB_USER/DB_PASSWORD) must be set in the environment.")

        return cls(
            host = os.getenv("DB_HOST", "127.0.0.1"),
            port = int(os.getenv("DB_PORT", "3306")),
            user = user,
            password = password,
            database = os.getenv("DB_NAME", "network_device_scanner")
        )

    def connector_args(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": "utf8mb4",
            "autocommit": False,        # We control commit/rollback (unit of work)
        }

