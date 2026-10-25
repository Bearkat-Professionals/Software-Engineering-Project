"""3.5.3 Verify Backup: confirm a backup is complete and readable.

Finding a problem is a normal answer, not an error: verify_backup() always
returns a VerificationResult listing every problem found (none means the
backup is good). Restore (3.5.4) verifies before it touches the database.
"""
from dataclasses import dataclass
from typing import Any

from backend.black_boxes.backup_media import BackupMedia, BackupMediaError
from backend.data_access.backup_recovery.backup_format import (Backup, BackupFormatError,
                                                               rows_checksum)


@dataclass(frozen=True)
class VerificationResult:
    name: str
    problems: tuple[str, ...]           # plain-English descriptions; empty when the backup is good
    backup: Backup | None = None        # the checked backup, only when it passed

    @property
    def ok(self) -> bool:
        return not self.problems


def verify_backup(media: BackupMedia, name: str) -> VerificationResult:
    """Load a stored backup and check it."""
    try:
        data = media.load(name)
    except BackupMediaError as exc:                 # missing, damaged, not gzip, not JSON
        return VerificationResult(name, (str(exc),))
    return check_backup(name, data)


def check_backup(name: str, data: Any) -> VerificationResult:
    """Check backup data that is already loaded: structure, then every table
    against the manifest's row count and checksum."""
    try:
        backup = Backup.from_dict(data)
    except BackupFormatError as exc:
        return VerificationResult(name, (str(exc),))
    if not isinstance(backup.tables, dict):
        return VerificationResult(name, ("the rows are not stored table by table",))

    listed = set(backup.manifest.tables)            # what the manifest says is there
    present = set(backup.tables)                    # what is actually there
    problems: list[str] = []
    if not listed:
        problems.append("the backup contains no tables")
    for table in sorted(listed - present):
        problems.append(f"table '{table}' is in the manifest but its rows are missing")
    for table in sorted(present - listed):
        problems.append(f"table '{table}' has rows but is not in the manifest")

    for table in sorted(listed & present):
        rows = backup.tables[table]
        expected = backup.manifest.tables[table]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            problems.append(f"table '{table}': the rows are not a list of records")
            continue
        if len(rows) != expected.row_count:
            problems.append(f"table '{table}': has {len(rows)} rows, the manifest says {expected.row_count}")
        if rows_checksum(rows) != expected.checksum:
            problems.append(f"table '{table}': checksum does not match, so the rows were changed or damaged")

    if problems:
        return VerificationResult(name, tuple(problems))
    return VerificationResult(name, (), backup)
