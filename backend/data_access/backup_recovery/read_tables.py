"""Reads tables and rows for backups: the shared reading step of 3.5.1 and 3.5.2,
and the column list 3.5.4 needs to put rows back.

The tables come from the database itself (information_schema), not from the
entity classes, so a backup holds every table as it really is, including the
alert_setting_event link table, which has no entity class.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Iterable

from backend.data_access.backup_recovery.backup_format import Row
from backend.data_access.database_engine import DatabaseEngine


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: list[str]          # every column except generated ones (MySQL calculates those itself)
    primary_key: list[str]      # rows are read in this order, so checksums are repeatable


def list_tables(cursor: Any) -> list[TableInfo]:
    """Every table in the current database, with its writable columns and primary key."""
    cursor.execute(
        "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
        "       COLUMN_KEY AS column_key, EXTRA AS extra "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() "
        "  AND TABLE_NAME IN (SELECT TABLE_NAME FROM information_schema.TABLES "
        "                     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE') "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    )
    columns: dict[str, list[str]] = {}
    keys: dict[str, list[str]] = {}
    for row in cursor.fetchall():
        table = row["table_name"]
        columns.setdefault(table, [])
        keys.setdefault(table, [])
        if "GENERATED" in row["extra"].upper():     # e.g. device.mac_unique_key
            continue
        columns[table].append(row["column_name"])
        if row["column_key"] == "PRI":
            keys[table].append(row["column_name"])
    return [TableInfo(name, columns[name], keys[name]) for name in columns]


def read_snapshot(engine: DatabaseEngine, table_names: Iterable[str] | None = None) -> tuple[str, dict[str, list[Row]]]:
    """Read every row of the chosen tables (all tables if None) in ONE transaction.

    Returns (database name, {table: rows}). Inside one InnoDB transaction every
    SELECT sees the database as it was at the first read, so a scan saving
    devices meanwhile can't leave the backup half old and half new.
    """
    with engine.unit_of_work() as cursor:
        cursor.execute("SELECT DATABASE() AS name")
        database = cursor.fetchone()["name"]

        tables = {table.name: table for table in list_tables(cursor)}
        wanted = list(tables) if table_names is None else list(table_names)
        unknown = [name for name in wanted if name not in tables]
        if unknown:
            raise ValueError(f"no such table(s) in '{database}': {', '.join(unknown)}")

        return database, {name: _read_rows(cursor, tables[name]) for name in wanted}


def _read_rows(cursor: Any, table: TableInfo) -> list[Row]:
    columns = ", ".join(f"`{column}`" for column in table.columns)
    order = ", ".join(f"`{column}`" for column in table.primary_key) or columns
    cursor.execute(f"SELECT {columns} FROM `{table.name}` ORDER BY {order}")
    return [{column: json_safe(value) for column, value in row.items()} for row in cursor.fetchall()]


def json_safe(value: Any) -> Any:
    """Turn a value MySQL returns into one JSON can store unchanged."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()            # 2026-10-25T14:03:00.123456
    if isinstance(value, timedelta):        # MySQL TIME columns come back as timedelta
        return str(value)
    if isinstance(value, Decimal):
        return str(value)                   # text keeps every digit; a float could lose some
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return value
