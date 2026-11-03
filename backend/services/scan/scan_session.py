"""Scan Session Controller (2.2.1): open (2.2.1.1, #45) and close (2.2.1.4, #46) a scan.

Only one scan runs at a time (CM-15). The database is the only record of which scan
is running, so every controller object, in any request or thread, sees the same answer.

    sessions = ScanSessionController(ScanStore(engine), UserStore(engine))
    sessions.recover_stuck_scans()                  # once, when the server starts (#50)
    result = sessions.open("192.168.1.0/24", user_id, ScanSettings.from_env())
    ...
    sessions.close(result.scan.scan_id, ScanOutcome.PARTIAL, ["ping unavailable: ..."])

The Scan Manager (#48) decides the outcome; close() only records it (design 8.4).
"""
from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from backend.data_access.network_data import ScanNotRunningError
from backend.data_access.repository import RecordNotFoundError
from backend.records import User
from backend.records.enums import ScanOutcome
from backend.records.scan import Scan
from backend.services.scan.scan_settings import ScanSettings
from backend.shared_modules.validate_input import validate_scope

STUCK_SCAN_MESSAGE = "the server stopped during this scan"

# error_message is TEXT (65,535 bytes) and utf8mb4 uses up to 4 bytes a character,
# so 10,000 characters always fits, with room to spare.
MAX_ERROR_LINES = 50
MAX_LINE_CHARS = 500
MAX_ERROR_CHARS = 10_000


class ScanStoreLike(Protocol):
    """The parts of 3.1's ScanStore used here; tests pass a fake."""
    def find(self, scan_id: int) -> Scan | None: ...
    def find_running(self) -> Scan | None: ...
    def start(self, scan: Scan) -> Scan: ...
    def finish(self, scan_id: int, outcome: ScanOutcome, error_message: str | None = None) -> Scan: ...


class UserFinder(Protocol):
    def find(self, user_id: int) -> User | None: ...


@dataclass(frozen=True)
class OpenResult:
    opened: bool
    scan: Scan | None = None
    reason: str | None = None           # why it wasn't opened, in plain words


@dataclass(frozen=True)
class _Closing:
    outcome: ScanOutcome
    error_message: str | None


def format_errors(errors: Iterable[object] | str) -> str | None:
    """The errors one per line, shortened to fit; None when there are none."""
    if isinstance(errors, str):
        errors = [errors]                                   # one message: keep it whole
    lines = [" ".join(str(error).split()) for error in errors]  # newlines inside a message -> spaces
    lines = [line for line in lines if line]
    if not lines:
        return None

    kept: list[str] = []
    used = 0
    for line in lines:
        line = line[:MAX_LINE_CHARS]
        if len(kept) == MAX_ERROR_LINES or used + len(line) + 1 > MAX_ERROR_CHARS:
            break
        kept.append(line)
        used += len(line) + 1
    hidden = len(lines) - len(kept)
    if hidden:
        kept.append(f"... and {hidden} more")
    return "\n".join(kept)


class ScanSessionController:
    def __init__(self, scan_store: ScanStoreLike, user_store: UserFinder) -> None:
        self.scan_store = scan_store
        self.user_store = user_store
        self.lock = threading.Lock()
        self._pending_closes: dict[int, _Closing] = {}      # closes whose save failed; retried by open()

    def recover_stuck_scans(self) -> int:
        """Call once when the server starts: any scan still 'running' was cut off by a stop
        or crash, so it's closed as failed. Returns how many were closed."""
        with self.lock:
            closed = 0
            while (stuck := self.scan_store.find_running()) is not None:
                self.scan_store.finish(stuck.scan_id, ScanOutcome.FAILED, STUCK_SCAN_MESSAGE)
                closed += 1
            return closed

    def open(self, scope: str, user_id: int, settings: ScanSettings) -> OpenResult:
        """Start a scan, unless the request is invalid or another scan is running (CM-15)."""
        with self.lock:
            checked = validate_scope(scope)
            if not checked:
                return OpenResult(False, reason=checked.reason)
            if self.user_store.find(user_id) is None:
                return OpenResult(False, reason="Your account was not found. Sign in again.")
            self._retry_pending_closes()
            running = self.scan_store.find_running()
            if running is not None:
                return OpenResult(False, reason=self._still_running_message(running))
            scan = self.scan_store.start(Scan(user_id=user_id, target_scope=checked.value,
                                              configuration_used=settings.to_configuration()))
            return OpenResult(True, scan=scan)

    def close(self, scan_id: int, outcome: ScanOutcome | str,
              errors: Iterable[object] | str = ()) -> Scan | None:
        """Record that a scan ended: its outcome, end time and errors (2.2.1.4).

        Closing a scan that's already closed changes nothing and returns it as stored.
        If saving fails, the error is raised and the next open() tries the close again.
        """
        closing = _Closing(ScanOutcome(outcome), format_errors(errors))    # bad outcome: ValueError, nothing saved
        with self.lock:
            return self._finish(scan_id, closing)

    def _finish(self, scan_id: int, closing: _Closing) -> Scan | None:
        try:
            scan = self.scan_store.finish(scan_id, closing.outcome, closing.error_message)
        except ScanNotRunningError:
            self._pending_closes.pop(scan_id, None)
            return self.scan_store.find(scan_id)            # already closed: leave it as it is
        except RecordNotFoundError:
            self._pending_closes.pop(scan_id, None)
            raise                                           # a wrong id: retrying won't help
        except Exception:
            self._pending_closes[scan_id] = closing         # e.g. the database is down: try again later
            raise
        self._pending_closes.pop(scan_id, None)
        return scan

    def _retry_pending_closes(self) -> None:
        for scan_id, closing in list(self._pending_closes.items()):
            self._finish(scan_id, closing)                  # still failing: raises, as open() would anyway

    def _still_running_message(self, running: Scan) -> str:
        starter = self.user_store.find(running.user_id)
        who = starter.username if starter is not None else "another user"
        when = f"at {running.started_at:%H:%M}" if running.started_at is not None else "earlier"
        return f"A scan started by {who} {when} is still running."