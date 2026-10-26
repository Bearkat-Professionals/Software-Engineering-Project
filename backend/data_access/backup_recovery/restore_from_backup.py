"""3.5.4 Restore From Backup: put a backup's rows back into the database.

All or nothing (CM-04): the backup is verified first, and every table is
cleared and refilled inside ONE transaction. If anything fails (a damaged
backup, a schema that no longer matches, a broken link between tables) the
transaction rolls back and the database is exactly as it was before.

The "are you sure?" confirmation is not here; it belongs to 2.5.2 Request
Restore, which decides whether to call this at all.
"""
from dataclasses import dataclass
from typing import Any

from backend.black_boxes.backup_media import BackupMedia
from backend.data_access.backup_recovery.backup_format import Backup, BackupKind, Row
from backend.data_access.backup_recovery.read_tables import TableInfo, list_tables
from backend.data_access.backup_recovery.verify_backup import check_backup
from backend.data_access.database_engine import DatabaseEngine


class RestoreError(Exception):
    """The backup was not restored; the database has not been changed."""


@dataclass(frozen=True)
class RestoreResult:
    name: str
    kind: BackupKind
    rows_restored: dict[str, int]       # table -> rows put back


def restore_from_backup(engine: DatabaseEngine, media: BackupMedia, name: str) -> RestoreResult:
    """Replace the backed-up tables' rows with the backup's rows.

    A full backup replaces every table; a partial backup replaces only its own
    tables and leaves the rest alone. BackupMediaError (missing or unreadable
    file) and RestoreError both mean nothing was changed.
    """
    data = media.load(name)
    checked = check_backup(name, data)              # verify exactly the data that will be used
    if not checked.ok:
        raise RestoreError(f"backup '{name}' failed verification: " + "; ".join(checked.problems))
    backup = checked.backup

    with engine.unit_of_work() as cursor:
        current = {table.name: table for table in list_tables(cursor)}
        _check_backup_fits_schema(backup, current)

        # Foreign keys are switched off only for this transaction, so tables can be
        # emptied and refilled in any order. _check_links() then makes sure nothing
        # was left pointing at a row that doesn't exist, before anything is committed.
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in backup.tables:
                cursor.execute(f"DELETE FROM `{table}`")   # DELETE, not TRUNCATE: TRUNCATE commits at once
            for table, rows in backup.tables.items():
                _insert_rows(cursor, table, rows)
        finally:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        _check_links(cursor, set(backup.tables))

    return RestoreResult(name, backup.manifest.kind,
                         {table: len(rows) for table, rows in backup.tables.items()})


def _check_backup_fits_schema(backup: Backup, current: dict[str, TableInfo]) -> None:
    """The backup's tables and columns must all still exist in the database."""
    missing_tables = sorted(set(backup.tables) - set(current))
    if missing_tables:
        raise RestoreError(f"the database has no table(s): {', '.join(missing_tables)}")

    if backup.manifest.kind == BackupKind.FULL:
        not_in_backup = sorted(set(current) - set(backup.tables))
        if not_in_backup:
            raise RestoreError("this full backup does not cover table(s) the database now has: "
                               + ", ".join(not_in_backup))

    for table, rows in backup.tables.items():
        if rows:
            unknown = sorted(set(rows[0]) - set(current[table].columns))
            if unknown:
                raise RestoreError(f"table '{table}' no longer has column(s): {', '.join(unknown)}")


def _insert_rows(cursor: Any, table: str, rows: list[Row]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    column_list = ", ".join(f"`{column}`" for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    cursor.executemany(f"INSERT INTO `{table}` ({column_list}) VALUES ({placeholders})",
                       [tuple(row.get(column) for column in columns) for row in rows])


def _check_links(cursor: Any, restored: set[str]) -> None:
    """With foreign keys switched off, MySQL didn't check the rows that went in.
    Look for any row that now points at a missing parent row (e.g. a device whose
    vendor_id no longer exists after a partial restore of the vendor table)."""
    cursor.execute(
        "SELECT TABLE_NAME AS child, COLUMN_NAME AS child_column, "
        "       REFERENCED_TABLE_NAME AS parent, REFERENCED_COLUMN_NAME AS parent_column "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
    )
    for link in cursor.fetchall():
        if link["child"] not in restored and link["parent"] not in restored:
            continue                                # neither side changed
        cursor.execute(
            f"SELECT COUNT(*) AS broken FROM `{link['child']}` AS c "
            f"LEFT JOIN `{link['parent']}` AS p ON c.`{link['child_column']}` = p.`{link['parent_column']}` "
            f"WHERE c.`{link['child_column']}` IS NOT NULL AND p.`{link['parent_column']}` IS NULL"
        )
        broken = cursor.fetchone()["broken"]
        if broken:
            raise RestoreError(
                f"{broken} row(s) in '{link['child']}' would point at a missing '{link['parent']}' "
                f"(via {link['child_column']}); restore cancelled")
