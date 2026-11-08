""" BB-8 Database Engine (black box)

Hides the MySQL driver, connection pool, and the transaction handling. Callers
see only: unit_of_work(), fetch_all(), fetch_one(), execute(), and the two
exceptions below. No caller needs to import mysql.connector.

Only modules inside of backend/data_access should import this file.
"""

from contextlib import contextmanager
from typing import Any, Optional, Sequence, Generator

import mysql.connector
from mysql.connector import pooling
from mysql.connector.constants import ClientFlag

from backend.config import DatabaseConfig

class DatabaseConnectionError(Exception):
    """Raised when a database connection cannot be established."""

class IntegrityConstraintError(Exception):
    """Raised when a statement breaks a database rule: a duplicate unique value (DI-07),
       a foreign key that is missing or still in use (DI-06), or a required column left empty.
       The transaction has already been rolled back.
    """

class LockNotAvailableError(Exception):
    """Raised by named_lock() when another connection held the lock for the whole wait."""

class DatabaseEngine:
    def __init__(self, config: DatabaseConfig, pool_size: int = 5):
        self._config = config

        try:
            self._pool = pooling.MySQLConnectionPool(
                pool_name="ndi_pool",
                pool_size=pool_size,
                pool_reset_session=True,
                client_flags=[ClientFlag.FOUND_ROWS],
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
        """Run several statements as one transaction (CM-04).

        Commits if the block finishes, rolls everything back if it raises.
        Yields a dictionary cursor (rows come back as dicts).
        """
        connection = self._pool.get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            yield cursor
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, mysql.connector.IntegrityError):
                 #  Translate the driver's error so callers stay database independent
                 raise IntegrityConstraintError(exc.msg) from exc
            raise
        finally:
            cursor.close()
            connection.close()  # return connection to the pool

    @contextmanager
    def named_lock(self, name: str, timeout_s: float) -> Generator[Any, None, None]:
        """Hold a MySQL named lock while running one transaction, all on one connection.
        Every program using this database shares the lock (unlike a threading.Lock).
        Waits up to timeout_s for it, then raises LockNotAvailableError. The block's
        work is committed BEFORE the lock is released, so the next holder sees it.
        MySQL frees the lock by itself if the connection drops.
        """
        connection = self._pool.get_connection()
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("SELECT GET_LOCK(%s, %s) AS got", (name, timeout_s))
            row = cursor.fetchone()
            if row is None or row["got"] != 1:           
                raise LockNotAvailableError(f"could not get the lock '{name}' within {timeout_s} s")
            connection.commit()                          
            try:
                yield cursor
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if isinstance(exc, mysql.connector.IntegrityError):
                    raise IntegrityConstraintError(exc.msg) from exc
                raise
            finally:
                cursor.execute("SELECT RELEASE_LOCK(%s) AS released", (name,))
                cursor.fetchall()
        finally:
            cursor.close()
            connection.close()  

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
