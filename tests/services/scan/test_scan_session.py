import threading
import time
import unittest
from datetime import datetime
from backend.records.scan import Scan
from backend.records.user import User
from backend.services.scan.scan_session import ScanSessionController
from backend.services.scan.scan_settings import ScanSettings






Javier = User(user_id = 1, username="Javier", password_hash="HateThis")
Jason = User(user_id = 2, username="Jason", password_hash="HateThis")
SCOPE = "192.168.1.0/24"
SETTINGS = ScanSettings()

class FakeScan_Database:
    def __init__(self):
        self.scans = []

    def find_running(self):
        for scan in self.scans:
            if scan.status == "running":
                return scan
        return None




    def start(self, scan):
        time.sleep(0.05)
        if scan.started_at is None:
            scan.started_at = datetime.now()
        scan.scan_id = len(self.scans) + 1
        scan.status = "running"
        self.scans.append(scan)
        return scan




    def finish(self, scan_id, outcome, error_message=None):
        for scan in self.scans:
            if scan.scan_id == scan_id:
                scan.status = "completed"
                scan.outcome = outcome
                scan.error_message = error_message





class FakeUserTable:
    def find(self, user_id):
        if user_id == 1:
            return Javier
        elif user_id == 2:
            return Jason
        return None





class TestScanSession(unittest.TestCase):

    def test_open_creates_scan(self):
        store = FakeScan_Database()
        sessions = ScanSessionController(store, FakeUserTable())

        result = sessions.open(SCOPE, 1, SETTINGS)


        self.assertTrue(result.opened)
        self.assertEqual(result.scan.user_id, 1)
        self.assertEqual(result.scan.target_scope, SCOPE)
        self.assertIsNotNone(result.scan.started_at)
        self.assertEqual(result.scan.configuration_used["probe_workers"], 32)




    def test_second_scan_is_refused(self):
        store = FakeScan_Database()
        sessions = ScanSessionController(store, FakeUserTable())
        first = sessions.open(SCOPE, 1, SETTINGS)
        first.scan.started_at = datetime(2026, 10, 30, 14 ,3)
        second = sessions.open(SCOPE, 2, SETTINGS)


        self.assertEqual(second.reason, "A scan started by Javier at 14:03 is still running.")



    def test_bad_scope_is_rejected(self):
        store = FakeScan_Database()
        sessions = ScanSessionController(store, FakeUserTable())
        result = sessions.open("8.8.8.0/24", 1, SETTINGS)

        self.assertFalse(result.opened)
        self.assertEqual(len(store.scans), 0)



    def test_stuck_scan_is_closed(self):
        store = FakeScan_Database()
        sessions = ScanSessionController(store, FakeUserTable())
        old_scan = store.start(Scan(user_id=2, target_scope=SCOPE))

        sessions.recover_stuck_scans()
        result = sessions.open(SCOPE, 1, SETTINGS)


        self.assertTrue(result.opened)
        self.assertEqual(old_scan.outcome, "failed")




    def testTwoScansAtOnce(self):
        store = FakeScan_Database()
        sessions = ScanSessionController(store, FakeUserTable())


        def Javier_scan():
            sessions.open(SCOPE, 1, SETTINGS)

        def Jason_scan():
            sessions.open(SCOPE, 2, SETTINGS)



        click1 = threading.Thread(target=Javier_scan)
        click2 = threading.Thread(target=Jason_scan)
        click1.start()
        click2.start()
        click1.join()
        click2.join()



        self.assertEqual(len(store.scans), 1)




if __name__ == "__main__":
    unittest.main()
                                         
                                         
                                         
                                



