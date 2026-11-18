"""2.5 Backup and Recovery (OOD BackupRecoveryService): the service behind the backup screen.

    service = BackupRecoveryService(engine, BackupMedia.from_env(), UserStore(engine), ScanStore(engine))
    service.create_backup(user_id)                      # 2.5.1 Request Backup
    service.list_backups()                              # 2.5.3 Report Backup Status
    service.verify("full-20261117-140500-123")          # problems, or none
    service.restore(name, confirm=typed_name, acting_user_id=user_id,
                    on_start=end_other_sessions)        # 2.5.2 Request Restore

One backup or restore runs at a time. A restore must be confirmed by typing the backup's
name, is refused while a scan runs, is verified first, takes a safety backup
(before-restore-<timestamp>) before it changes anything, and is audited.

The 3.5 engine functions are passed in, so unit tests need no database. Errors are
reported in plain words without file paths; the details go to the server log.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from backend.black_boxes.backup_media import BackupMedia, BackupMediaError, StoredBackup
from backend.data_access.audit_trail.audit_trail import AuditTrail
from backend.data_access.backup_recovery.create_full_backup import create_full_backup
from backend.data_access.backup_recovery.restore_from_backup import RestoreError, RestoreResult, restore_from_backup
from backend.data_access.backup_recovery.verify_backup import VerificationResult, verify_backup
from backend.data_access.database_engine import DatabaseEngine, LockNotAvailableError
from backend.data_access.network_data.scan_store import SCAN_OPEN_LOCK
from backend.records import Scan, User
from backend.records.enums import AlertEventName

logger = logging.getLogger(__name__)

SAFETY_PREFIX = "before-restore"
SCAN_LOCK_WAIT_S = 5
CONFIRM_REASON = "Type the backup's name exactly to confirm."
SCAN_RUNNING_REASON = "A scan is running. Restore after it finishes."
RESTORE_RECORD_TYPE = "restore"         

class BackupStatus(str, Enum):
    DONE = "done"
    REJECTED = "rejected"          
    NOT_FOUND = "not_found"         
    BUSY = "busy"                   
    FAILED = "failed"               

class BackupAction(str, Enum):
    BACKUP = "backup"
    RESTORE = "restore"

@dataclass(frozen=True)
class BackupResult:
    status: BackupStatus
    reasons: tuple[str, ...] = ()                     
    backup: StoredBackup | None = None                
    safety_backup: str | None = None                    
    rows_restored: dict[str, int] = field(default_factory=dict)     
    problems: tuple[str, ...] = ()                      
    alerts: tuple[AlertEventName, ...] = ()             

    @property
    def ok(self) -> bool:
        return self.status is BackupStatus.DONE

@dataclass(frozen=True)
class LastAttempt:
    """The most recent backup or restore that was actually tried (kept in memory)."""
    action: BackupAction
    name: str | None                
    by: str                         
    at: datetime
    status: BackupStatus
    reason: str | None = None

@dataclass(frozen=True)
class Running:
    """The backup or restore running right now."""
    action: BackupAction
    by: str
    since: datetime

@dataclass(frozen=True)
class ListedBackup:
    name: str
    kind: str                       
    created: str                    
    size_bytes: int

@dataclass(frozen=True)
class BackupListing:
    backups: tuple[ListedBackup, ...]           
    last_attempt: LastAttempt | None
    busy: Running | None

class UserFinder(Protocol):
    def find(self, user_id: int, unit: Any = None) -> User | None: ...

class RunningScanFinder(Protocol):
    def find_running(self, unit: Any = None) -> Scan | None: ...

class BackupRecoveryService:
    def __init__(self, engine: DatabaseEngine, media: BackupMedia, users: UserFinder, scans: RunningScanFinder, *,
                 create: Callable[..., StoredBackup] = create_full_backup,
                 verify: Callable[[BackupMedia, str], VerificationResult] = verify_backup,
                 restore: Callable[[DatabaseEngine, BackupMedia, str], RestoreResult] = restore_from_backup,
                 audit: AuditTrail | None = None, clock: Callable[[], datetime] = datetime.now,
                 scan_lock_wait_s: float = SCAN_LOCK_WAIT_S) -> None:
        self._engine = engine
        self._media = media
        self._users = users
        self._scans = scans
        self._create = create
        self._verify = verify
        self._restore = restore
        self._audit = audit if audit is not None else AuditTrail(engine)
        self._clock = clock
        self._scan_lock_wait_s = scan_lock_wait_s
        self._state_lock = threading.Lock()            
        self._running: Running | None = None
        self._last: LastAttempt | None = None

    #2.5.1 Request Backup
    def create_backup(self, acting_user_id: int) -> BackupResult:
        """Take a full backup now. FAILED (with the backup-failed alert) if it can't be taken."""
        by = self._username(acting_user_id)
        busy = self._claim(BackupAction.BACKUP, by)
        if busy is not None:
            return busy
        try:
            stored = self._create(self._engine, self._media)
        except Exception as exc:
            logger.exception("backup by %s failed", by)
            result = BackupResult(BackupStatus.FAILED, (f"The backup failed: {_why(exc)}",),
                                  alerts=(AlertEventName.BACKUP_FAILED,))
            name = None
        else:
            logger.info("backup %s taken by %s", stored.name, by)
            result = BackupResult(BackupStatus.DONE, backup=stored)
            name = stored.name
        finally:
            self._release()
        self._remember(BackupAction.BACKUP, name, by, result)
        return result

    #2.5.3 Report Backup Status
    def list_backups(self) -> BackupListing:
        """Every stored backup, newest first, with the last attempt and what's running now."""
        backups = tuple(ListedBackup(stored.name, backup_kind(stored.name), stored.modified_at, stored.size_bytes)
                        for stored in self._media.list())
        with self._state_lock:
            return BackupListing(backups, self._last, self._running)

    def verify(self, name: str) -> BackupResult:
        """Check one backup. DONE with its problems; no problems means it's good."""
        if not self._exists(name):
            return _not_found(name)
        checked = self._verify(self._media, name)
        logger.info("backup %s verified: %s", name, "good" if not checked.problems else "problems found")
        return BackupResult(BackupStatus.DONE, problems=tuple(checked.problems))

    #2.5.2 Request Restore
    def restore(self, name: str, confirm: str, acting_user_id: int, *,
                on_start: Callable[[], None] = lambda: None) -> BackupResult:
        """Replace the database with a backup, in this order, stopping at the first refusal:
        the confirmation, the name, another backup or restore, a running scan, verification,
        the safety backup, on_start() (the API signs everyone else out here), the restore,
        then one audit entry."""
        if confirm != name:
            return BackupResult(BackupStatus.REJECTED, (CONFIRM_REASON,))
        if not self._exists(name):
            return _not_found(name)
        by = self._username(acting_user_id)
        busy = self._claim(BackupAction.RESTORE, by)
        if busy is not None:
            return busy
        try:
            result = self._restore_holding_the_scan_lock(name, acting_user_id, by, on_start)
        except Exception as exc:                        
            logger.exception("restore of %s by %s stopped", name, by)
            result = BackupResult(BackupStatus.FAILED, (f"Nothing was changed: {_why(exc)}",))
        finally:
            self._release()
        log = logger.warning if result.ok else logger.info
        log("restore of %s by %s: %s%s", name, by, result.status.value,
            f" ({'; '.join(result.reasons)})" if result.reasons else "")
        self._remember(BackupAction.RESTORE, name, by, result)
        return result

    def _restore_holding_the_scan_lock(self, name: str, user_id: int, by: str,
                                       on_start: Callable[[], None]) -> BackupResult:
        """The scan-open lock is held the whole time, so no scan can start during a restore."""
        try:
            with self._engine.named_lock(SCAN_OPEN_LOCK, self._scan_lock_wait_s) as cursor:
                if self._scans.find_running(cursor) is not None:
                    return BackupResult(BackupStatus.BUSY, (SCAN_RUNNING_REASON,))
                return self._restore_now(name, user_id, by, on_start)
        except LockNotAvailableError:                   
            return BackupResult(BackupStatus.BUSY, (SCAN_RUNNING_REASON,))

    def _restore_now(self, name: str, user_id: int, by: str, on_start: Callable[[], None]) -> BackupResult:
        checked = self._verify(self._media, name)
        if checked.problems:
            return BackupResult(BackupStatus.REJECTED, ("The backup didn't pass verification, so nothing was changed.",),
                                problems=tuple(checked.problems))

        safety_name = f"{SAFETY_PREFIX}-{self._clock():%Y%m%d-%H%M%S-%f}"[:-3]     
        try:
            safety = self._create(self._engine, self._media, name=safety_name)
        except Exception as exc:
            logger.exception("safety backup before restoring %s failed", name)
            return BackupResult(BackupStatus.FAILED,
                                (f"The safety backup couldn't be taken, so nothing was restored: {_why(exc)}",),
                                alerts=(AlertEventName.BACKUP_FAILED,))

        on_start()
        try:
            restored = self._restore(self._engine, self._media, name)
        except RestoreError as exc:
            return BackupResult(BackupStatus.FAILED, (f"Nothing was changed: {exc}",), safety_backup=safety.name)
        except BackupMediaError:
            logger.exception("backup %s couldn't be read for the restore", name)
            return BackupResult(BackupStatus.FAILED, ("Nothing was changed: the backup couldn't be read.",),
                                safety_backup=safety.name)

        self._audit_restore(user_id, name, safety.name, restored.rows_restored)
        return BackupResult(BackupStatus.DONE, safety_backup=safety.name, rows_restored=dict(restored.rows_restored))

    def _audit_restore(self, user_id: int, name: str, safety_name: str, rows: dict[str, int]) -> None:
        """One audit entry, if the admin who restored exists in the restored accounts
        (audit_log.user_id is a foreign key to app_user). The restore stands either way."""
        try:
            with self._engine.unit_of_work() as unit:
                if self._users.find(user_id, unit) is None:
                    logger.warning("restore of %s not audited: user %s isn't in the restored accounts, "
                                   "and audit_log.user_id must point at one", name, user_id)
                    return
                self._audit.record_event(user_id, RESTORE_RECORD_TYPE, 0,
                                         {"backup": name, "safety_backup": safety_name, "rows_restored": dict(rows)},
                                         unit)
        except Exception:
            logger.exception("restore of %s finished, but its audit entry couldn't be written", name)

    #helpers
    def _claim(self, action: BackupAction, by: str) -> BackupResult | None:
        """Mark this action as running, or the BUSY result if one already is."""
        with self._state_lock:
            running = self._running
            if running is None:
                self._running = Running(action, by, self._clock())
                return None
        return BackupResult(BackupStatus.BUSY, (f"A {running.action.value} is already running "
                                                f"(started by {running.by} at {running.since:%H:%M}).",))

    def _release(self) -> None:
        with self._state_lock:
            self._running = None

    def _remember(self, action: BackupAction, name: str | None, by: str, result: BackupResult) -> None:
        attempt = LastAttempt(action, name, by, self._clock(), result.status,
                              " ".join(result.reasons) or None)
        with self._state_lock:
            self._last = attempt

    def _exists(self, name: str) -> bool:
        return any(stored.name == name for stored in self._media.list())

    def _username(self, user_id: int) -> str:
        try:
            user = self._users.find(user_id)
        except Exception:                              
            logger.exception("couldn't look up user %s", user_id)
            user = None
        return user.username if user is not None else f"user {user_id}"

def backup_kind(name: str) -> str:
    """What a backup is, from its name: full-..., partial-... or before-restore-... ("safety")."""
    if name.startswith(f"{SAFETY_PREFIX}-"):
        return "safety"
    for kind in ("full", "partial"):
        if name.startswith(f"{kind}-"):
            return kind
    return "other"

def _not_found(name: str) -> BackupResult:
    return BackupResult(BackupStatus.NOT_FOUND, (f"There's no backup named '{name}'.",))

def _why(exc: BaseException) -> str:
    """A reason for the screen, without file paths: those stay in the server log."""
    if isinstance(exc, BackupMediaError):
        cause = exc.__cause__
        detail = cause.strerror if isinstance(cause, OSError) and cause.strerror else None
        return "it couldn't be saved in the backup folder" + (f" ({detail})." if detail else ".")
    if isinstance(exc, OSError) and exc.strerror:
        return f"{exc.strerror}."
    return f"the database reported an error ({type(exc).__name__}); see the server log."