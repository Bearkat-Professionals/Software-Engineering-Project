import threading
from backend.records.enums import ScanOutcome
from backend.records.scan import Scan
from backend.services.scan.scan_settings import ScanSettings
from backend.shared_modules.validate_input import validate_scope

STUCK_SCAN_MESSAGE = "the server stopped during this scan"


class OpenResult:
    def __init__(self, opened, scan=None, reason=None):
        self.opened = opened
        self.scan = scan
        self.reason = reason


class ScanSessionController:
    def __init__(self, scan_store, user_store):
        self.scan_store = scan_store
        self.user_store = user_store
        self.lock = threading.Lock()

    def recover_stuck_scans(self) -> int:
        closed = 0
        while (stuck := self.scan_store.find_running()) is not None:
            self.scan_store.finish(stuck.scan_id, ScanOutcome.FAILED, STUCK_SCAN_MESSAGE)
            closed += 1
        return closed

    def open(self, scope: str, user_id: int, settings: ScanSettings) -> OpenResult:
        with self.lock:
            checked = validate_scope(scope)
            if not checked:
                return OpenResult(False, reason=checked.reason)
            if self.user_store.find(user_id) is None:
                return OpenResult(False, reason="Your account was not found. Sign in again.")
            running = self.scan_store.find_running()
            if running is not None:
                return OpenResult(False, reason=self._still_running_message(running))
            scan = self.scan_store.start(Scan(user_id=user_id, target_scope=checked.value,
                                              configuration_used=settings.to_configuration()))
            return OpenResult(True, scan=scan)

    def _still_running_message(self, running: Scan) -> str:
        starter = self.user_store.find(running.user_id)
        who = starter.username if starter is not None else "another user"
        when = "at " + running.started_at.strftime("%H:%M") if running.started_at is not None else "earlier"
        return "A scan started by " + who + " " + when + " is still running."

    def close(self, scan_id: int, outcome: ScanOutcome, error_message: str | None = None) -> Scan:
        with self.lock:
            return self.scan_store.finish(scan_id, outcome, error_message)