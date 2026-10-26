"""The structure of a backup: what a backup holds and how its contents are checked

A backup is plain data, a manifest (what should be in it) plus the rows:

    {
        "manifest": {
            "format_version": 1,
            "kind": "full",
            "created_at": "2026-10-25T14:03:00",
            "database": "network_device_scanner",
            "tables": {"vendor": {"row_count": 3, "checksum": "9f2c..."}, ...}
        },
        "tables": {"vendor": [{"vendor_id": 1, "vendor_name": "unknown", ...}, ...], ...}
    }

Rows must already be JSON-safe (dates as ISO text), so the checksum taken when
the backup is made matches the one recomputed after it is written and read back.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any

FORMAT_VERSION = 1

Row = dict[str, Any]

class BackupFormatError(Exception):
    """The data is not a backup this version can read (missing parts, unknown version)."""

class BackupKind(str, Enum):
    FULL = "full"
    PARTIAL = "partial"

@dataclass(frozen=True)
class TableSummary:
    row_count: int
    checksum: str       # SHA-256 of the table's rows, see rows_checksum()

@dataclass(frozen=True)
class BackupManifest:
    kind: BackupKind
    created_at: str
    database: str
    tables: dict[str, TableSummary]
    format_version: int = FORMAT_VERSION

@dataclass(frozen=True)
class Backup:
    manifest: BackupManifest
    tables: dict[str, list[Row]]

    def to_dict(self) -> dict[str, Any]:
        """Plain dicts and lists, ready to be written as JSON."""
        manifest = asdict(self.manifest)
        manifest["kind"] = self.manifest.kind.value
        return {"manifest": manifest, "tables": self.tables}

    @classmethod
    def from_dict(cls, data: Any) -> "Backup":
        """Rebuild a Backup from to_dict() output. Raises BackupFormatError if parts are missing."""
        try:
            manifest = data["manifest"]
            if manifest["format_version"] != FORMAT_VERSION:
                raise BackupFormatError(
                    f"backup format version {manifest['format_version']} is not supported "
                    f"(this program reads version {FORMAT_VERSION})")
            summaries = {name: TableSummary(**summary) for name, summary in manifest["tables"].items()}
            return cls(
                manifest=BackupManifest(
                    kind=BackupKind(manifest["kind"]),
                    created_at=manifest["created_at"],
                    database=manifest["database"],
                    tables=summaries,
                ),
                tables=data["tables"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupFormatError(f"not a valid backup: {exc!r}") from exc


def rows_checksum(rows: list[Row]) -> str:
    """SHA-256 of the rows written in one fixed form, so the same rows always
    give the same checksum and any changed value gives a different one."""
    text = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_backup(kind: BackupKind, database: str, tables: dict[str, list[Row]]) -> Backup:
    """Wrap rows that were just read in a Backup, with a manifest describing them."""
    summaries = {name: TableSummary(row_count=len(rows), checksum=rows_checksum(rows))
                 for name, rows in tables.items()}
    manifest = BackupManifest(
        kind=kind,
        created_at=datetime.now().isoformat(timespec="seconds"),
        database=database,
        tables=summaries,
    )
    return Backup(manifest=manifest, tables=tables)

def backup_name(kind: BackupKind) -> str:
    """A file name like full-20261025-140300-123, to the millisecond so two
    backups made in the same second get different names."""
    return f"{kind.value}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}"
