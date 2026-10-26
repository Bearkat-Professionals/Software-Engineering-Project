"""Manual tool for trying backup and recovery (3.5) by hand. Not a unit test.

    py -3.13 -m backend.data_access.backup_recovery.check_backup full
    py -3.13 -m backend.data_access.backup_recovery.check_backup partial
    py -3.13 -m backend.data_access.backup_recovery.check_backup list
    py -3.13 -m backend.data_access.backup_recovery.check_backup verify NAME
    py -3.13 -m backend.data_access.backup_recovery.check_backup restore NAME
    py -3.13 -m backend.data_access.backup_recovery.check_backup compare DB_A DB_B

Uses the TEST database unless --database names another one. Backing up the
real database only reads it, but restoring into a database whose name does
not end in _test also needs --allow-real, and every restore asks first.
"""
import argparse
import os
from dataclasses import replace

from backend.black_boxes.backup_media import BackupMedia, BackupMediaError
from backend.config import DatabaseConfig
from backend.data_access.backup_recovery.backup_format import Backup, rows_checksum
from backend.data_access.backup_recovery.create_full_backup import create_full_backup
from backend.data_access.backup_recovery.create_partial_backup import create_partial_backup
from backend.data_access.backup_recovery.read_tables import read_snapshot
from backend.data_access.backup_recovery.restore_from_backup import RestoreError, restore_from_backup
from backend.data_access.backup_recovery.verify_backup import verify_backup
from backend.data_access.database_engine import DatabaseConnectionError, DatabaseEngine


def connect(database: str) -> DatabaseEngine:
    return DatabaseEngine(replace(DatabaseConfig.from_env(), database=database), pool_size=1)


def test_database() -> str:
    return os.getenv("TEST_DB_NAME", f"{DatabaseConfig.from_env().database}_test")


def show_tables(backup: Backup) -> None:
    for table, summary in backup.manifest.tables.items():
        print(f"  {table:<26} {summary.row_count:>6} rows")


# ---- one function per command ------------------------------------------------

def cmd_backup(args, media: BackupMedia, create) -> None:
    stored = create(connect(args.database), media)
    print(f"Saved {stored.name} ({stored.size_bytes:,} bytes) from '{args.database}':")
    show_tables(Backup.from_dict(media.load(stored.name)))


def cmd_list(args, media: BackupMedia) -> None:
    backups = media.list()
    if not backups:
        print("No backups yet.")
    for stored in backups:
        print(f"  {stored.name:<32} {stored.size_bytes:>10,} bytes   {stored.modified_at}")


def cmd_verify(args, media: BackupMedia) -> None:
    result = verify_backup(media, args.name)
    if result.ok:
        manifest = result.backup.manifest
        print(f"OK: {args.name} is a complete {manifest.kind.value} backup of "
              f"'{manifest.database}', made {manifest.created_at}:")
        show_tables(result.backup)
    else:
        print(f"PROBLEMS in {args.name}:")
        for problem in result.problems:
            print(f"  - {problem}")
        raise SystemExit(1)


def cmd_restore(args, media: BackupMedia) -> None:
    if not args.database.endswith("_test") and not args.allow_real:
        raise SystemExit(f"'{args.database}' is not a test database. Add --allow-real if you really mean it.")
    if not args.yes:
        answer = input(f"Replace the rows in '{args.database}' with backup '{args.name}'? Type yes to continue: ")
        if answer.strip().lower() != "yes":
            print("Cancelled; nothing was changed.")
            return
    result = restore_from_backup(connect(args.database), media, args.name)
    print(f"Restored {result.kind.value} backup {args.name} into '{args.database}':")
    for table, count in result.rows_restored.items():
        print(f"  {table:<26} {count:>6} rows")


def cmd_compare(args, media: BackupMedia) -> None:
    sides = []
    for database in (args.first, args.second):
        _, tables = read_snapshot(connect(database))
        sides.append({table: (len(rows), rows_checksum(rows)) for table, rows in tables.items()})
    first, second = sides

    print(f"  {'table':<26} {args.first:>26} {args.second:>26}")
    differences = 0
    for table in sorted(set(first) | set(second)):
        a, b = first.get(table), second.get(table)
        same = a == b
        differences += not same
        a_text = f"{a[0]} rows" if a else "missing"
        b_text = f"{b[0]} rows" if b else "missing"
        print(f"  {table:<26} {a_text:>26} {b_text:>26}   {'same' if same else 'DIFFERENT'}")
    print("All tables match." if not differences else f"{differences} table(s) differ.")


# ---- command line --------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Try backup and recovery by hand.")
    # Shared by the commands that work on one database
    database = argparse.ArgumentParser(add_help=False)
    database.add_argument("--database", default=None,
                          help="database to use (default: the test database, DB_NAME + '_test')")

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("full", parents=[database], help="back up every table")
    commands.add_parser("partial", parents=[database], help="back up the reference tables")
    commands.add_parser("list", help="list stored backups, newest first")
    verify = commands.add_parser("verify", help="check a backup is complete and unchanged")
    verify.add_argument("name")
    restore = commands.add_parser("restore", parents=[database], help="restore a backup (asks first)")
    restore.add_argument("name")
    restore.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    restore.add_argument("--allow-real", action="store_true", help="allow a database not ending in _test")
    compare = commands.add_parser("compare", help="compare two databases table by table")
    compare.add_argument("first")
    compare.add_argument("second")
    args = parser.parse_args()

    if getattr(args, "database", "unused") is None:
        args.database = test_database()
    media = BackupMedia.from_env()
    handlers = {
        "full": lambda: cmd_backup(args, media, create_full_backup),
        "partial": lambda: cmd_backup(args, media, create_partial_backup),
        "list": lambda: cmd_list(args, media),
        "verify": lambda: cmd_verify(args, media),
        "restore": lambda: cmd_restore(args, media),
        "compare": lambda: cmd_compare(args, media),
    }
    try:
        handlers[args.command]()
    except (BackupMediaError, RestoreError, DatabaseConnectionError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}")


if __name__ == "__main__":
    main()
