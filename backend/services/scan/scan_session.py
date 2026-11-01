import threading
from datetime import datetime
from backend.records.enums import ScanOutcome
from backend.records.scan import Scan
from backend.shared_modules.validate_input import validate_scope




ScanPing_Workers = 32
Ping_timeout_Seconds = 1
Discovery_Methods = ["arp", "ping"]
Stuck_Scan_message = "the server stopped during this scan"



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
        self.current_scan = None



    def open(self, scope, user_id):
        with self.lock:
            user = self.user_store.find(user_id)
            if not user:
                return OpenResult(False, reason="User not found")


            running_scan = self.scan_store.find_running()
            if running_scan and self.current_scan:
                starter = self.user_store.find(running_scan.user_id)
                when = running_scan.started_at.strftime("%H:%M")
                return OpenResult(False, reason="A scan started by " + starter.username + " at " + when + " is still running.")

            while running_scan:
                self.scan_store.finish(running_scan.scan_id, ScanOutcome.FAILED, Stuck_Scan_message)
                running_scan = self.scan_store.find_running()


            result = validate_scope(scope)
            if not result:
                return OpenResult(False, reason=result.reason)




            settings = {
                "max_workers": ScanPing_Workers,
                "timeout_seconds": Ping_timeout_Seconds,
                "discovery_methods": Discovery_Methods
            }

            new_scan = Scan(
                user_id = user_id,
                target_scope=result.value,
                configuration_used=settings,
                started_at=datetime.now().replace(microsecond=0)

            )
            saved_scan = self.scan_store.start(new_scan)
            self.current_scan = saved_scan
            return OpenResult(True, scan=saved_scan)


    def close(self, scan_id, outcome, error_message=None):
        with self.lock:
            finished = self.scan_store.finish(scan_id, outcome, error_message)
            self.current_scan = None
            return finished





  
