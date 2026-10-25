"""3.5.2 Create Partial Backup: copy selected tables to backup media (BB-9).

By default the reference data, which changes rarely and is backed up between
full backups (design section 14.2).
"""
from typing import Iterable

from backend.black_boxes.backup_media import BackupMedia, StoredBackup
from backend.data_access.backup_recovery.backup_format import BackupKind, backup_name, build_backup
from backend.data_access.backup_recovery.read_tables import read_snapshot
from backend.data_access.database_engine import DatabaseEngine

REFERENCE_TABLES = ("vendor", "oui_prefix", "device_type", "device_signature")


def create_partial_backup(engine: DatabaseEngine, media: BackupMedia,
                          tables: Iterable[str] = REFERENCE_TABLES) -> StoredBackup:
    """Back up only the chosen tables, read in one consistent snapshot. Returns where it was stored."""
    database, rows = read_snapshot(engine, tables)
    backup = build_backup(BackupKind.PARTIAL, database, rows)
    return media.save(backup_name(BackupKind.PARTIAL), backup.to_dict())
