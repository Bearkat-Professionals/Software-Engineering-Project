"""3.5.1 Create Full Backup: copy every table to backup media (BB-9)."""
from backend.black_boxes.backup_media import BackupMedia, StoredBackup
from backend.data_access.backup_recovery.backup_format import BackupKind, backup_name, build_backup
from backend.data_access.backup_recovery.read_tables import read_snapshot
from backend.data_access.database_engine import DatabaseEngine


def create_full_backup(engine: DatabaseEngine, media: BackupMedia, name: str | None = None) -> StoredBackup:
    """Back up every table, read in one consistent snapshot. Returns where it was stored.

    name is the backup's name, e.g. 2.5.2's safety backup "before-restore-...";
    by default a new full-<timestamp> name.
    
    Database and media errors are not caught here: 2.5 Backup and Recovery
    decides what a failure means (report it, send the backup-failed alert).
    """
    database, tables = read_snapshot(engine)
    backup = build_backup(BackupKind.FULL, database, tables)
    return media.save(name or backup_name(BackupKind.FULL), backup.to_dict())
