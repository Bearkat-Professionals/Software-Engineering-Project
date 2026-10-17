""" BB-8 Database Engine (black box)

Hides the MySQL driver, connection pool, and the transaction handling. Callers
see only: unit_0f_work(), fetch_all(), fetch_one(), execute().

Only modules inside of backend/data_access should import this file.
"""

from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence, Generator

import mysql.connector
from mysql.connector import pooling

from backend.config import DatabaseConfig

class DatabaseConnectionError(Exception):
    """Raised when a database connection cannot be established."""

class DatabaseEngine:
    def __init__(self, config: DatabaseConfig, pool_size: int = 5):
        self._config = config

        try:
            self._pool = pooling.MySQLConnectionPool(
                pool_name="ndi_pool",
                pool_size=pool_size,
                pool_reset_session=True,
                **config.connector_args(),
            )
        except mysql.connector.Error as exc:
            raise DatabaseConnectionError(
                f"Could not connect to MySQL at {config.host}:{config.port} "
                f"(database '{config.database}'): {exc}"
            ) from exc

    @classmethod
    def from_env(cls) -> "DatabaseEngine":
        return cls(DatabaseConfig.from_env())

    @contextmanager
    def unit_of_work(self) -> Generator[Any, None, None]:
        """Run serveral statements as one transaction (CM-04).

        Commits if the block finishes, rolls everything back if it raises.
        Yields a dictionary cursor (rows come back as dicts).
        """
        connection = self._pool.get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()  # return connection to the pool

    def fetch_all(self, sql: str, params: Sequence = ()) -> list[dict]:
        with self.unit_of_work() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def fetch_one(self, sql: str, params: Sequence = ()) -> Optional[dict]:
        with self.unit_of_work() as cursor:
                    cursor.execute(sql, params)
                    return cursor.fetchone()

    def execute(self, sql: str, params: Sequence = ()) -> int:
         """
         Run one INSERT/DELETE/UPDATE. Returns the new row id (for inserts).
         """
         with self.unit_of_work() as cursor:
                     cursor.execute(sql, params)
                     return cursor.lastrowid
