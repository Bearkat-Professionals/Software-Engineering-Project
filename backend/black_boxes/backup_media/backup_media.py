"""BB-9 Backup Media

Hides where backup files are kept and how they are stored on disk
(gzip-compressed JSON). Callers hand over plain data and a name; they never
build paths or open files. Storing backups somewhere else later (another
drive, a network share) changes only this module.

    media = BackupMedia.from_env()                  # BACKUP_DIR, or <project>/backups
    media.save("full-20261025-140300", backup.to_dict())
    data = media.load("full-20261025-140300")
"""

import gzip
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # goes up to the project root folder
DEFAULT_FOLDER = PROJECT_ROOT / "backups"           # listed in .gitignore: backups hold password hashes
SUFFIX = ".json.gz"

# Letters, digits, dot, dash, underscore; must start with a letter or digit.
# Blocks names like "../../secrets" that would escape the backup folder.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class BackupMediaError(Exception):
    """A backup could not be written or read (bad name, missing, damaged, disk problem)."""


@dataclass(frozen=True)
class StoredBackup:
    name: str
    size_bytes: int
    modified_at: str


class BackupMedia:
    def __init__(self, folder: Path):
        self._folder = Path(folder)

    @classmethod
    def from_env(cls) -> "BackupMedia":
        return cls(Path(os.getenv("BACKUP_DIR", DEFAULT_FOLDER)))

    def save(self, name: str, data: dict[str, Any]) -> StoredBackup:
        """Write a new backup. Never overwrites an existing one."""
        path = self._path_for(name)
        if path.exists():
            raise BackupMediaError(f"a backup named '{name}' already exists")
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            # Write to a temporary file first, then rename it into place. A crash or a
            # full disk part-way through leaves only a stray .tmp file, never a
            # half-written backup under the real name.
            handle, temp_name = tempfile.mkstemp(dir=self._folder, suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as zipped:
                    zipped.write(json.dumps(data).encode("utf-8"))
                os.replace(temp_name, path)
            except BaseException:
                Path(temp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise BackupMediaError(f"could not write backup '{name}': {exc}") from exc
        return self._describe(path)

    def load(self, name: str) -> dict[str, Any]:
        """Read a backup back into plain data. Raises BackupMediaError if it is missing or damaged."""
        path = self._path_for(name)
        if not path.exists():
            raise BackupMediaError(f"no backup named '{name}'")
        try:
            with gzip.open(path, "rb") as zipped:
                return json.loads(zipped.read().decode("utf-8"))
        except (OSError, EOFError, ValueError) as exc:     # bad gzip, cut-off file, not JSON
            raise BackupMediaError(f"backup '{name}' is damaged or unreadable: {exc}") from exc

    def list(self) -> list[StoredBackup]:
        """Every stored backup, newest first."""
        if not self._folder.exists():
            return []
        backups = [self._describe(path) for path in self._folder.glob(f"*{SUFFIX}")]
        return sorted(backups, key=lambda backup: backup.modified_at, reverse=True)

    # ---- helpers -----------------------------------------------------------

    def _path_for(self, name: str) -> Path:
        if not _SAFE_NAME.fullmatch(name) or ".." in name:
            raise BackupMediaError(f"'{name}' is not a valid backup name")
        return self._folder / f"{name}{SUFFIX}"

    @staticmethod
    def _describe(path: Path) -> StoredBackup:
        stat = path.stat()
        return StoredBackup(
            name=path.name.removesuffix(SUFFIX),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        )