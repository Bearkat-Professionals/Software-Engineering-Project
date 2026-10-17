"""Run from the project root:  python -m unittest discover tests"""
import json
import unittest
from datetime import datetime

from backend.records import (AlertEvent, AlertSetting, AuditLog, Device,
                             DeviceSignature, OuiPrefix, Scan, User, Vendor)
from backend.records.enums import (AlertEventName, AuditAction, EvidenceType,
                                   NetworkStatus, RecordStatus, ScanOutcome,
                                   ScanStatus)


class EntityTests(unittest.TestCase):
    def test_device_row_round_trip(self):
        row = {"device_id": 7, "mac_address": "AA:BB:CC:DD:EE:FF",
               "flagged_status": 1, "is_randomized_mac": 0,
               "network_status": "online", "confidence_score": 85,
               "unknown_column": "ignored"}
        device = Device.from_row(row)
        self.assertEqual(device.pk, 7)
        self.assertIs(device.flagged_status, True)
        self.assertIs(device.is_randomized_mac, False)
        self.assertEqual(device.network_status, NetworkStatus.ONLINE)
        out = device.to_row()
        self.assertNotIn("device_id", out)
        self.assertEqual(device.to_row(include_id=True)["device_id"], 7)
        self.assertEqual(out["network_status"], "online")

    def test_unset_device_type_is_left_for_the_database_default(self):
        device = Device(mac_address="AA:BB:CC:DD:EE:FF")
        self.assertNotIn("device_type_id", device.to_row())   # DB default = "unknown"
        device.device_type_id = 4
        self.assertEqual(device.to_row()["device_type_id"], 4)

    def test_record_sighting_updates_instead_of_duplicating(self):
        device = Device(mac_address="AA:BB:CC:DD:EE:FF")
        t1, t2 = datetime(2026, 10, 1), datetime(2026, 10, 2)
        device.record_sighting("192.168.1.5", t1)
        device.record_sighting("192.168.1.9", t2, hostname="echo")
        self.assertEqual(device.first_seen, t1)
        self.assertEqual(device.last_seen, t2)
        self.assertEqual(device.current_ip, "192.168.1.9")

    def test_unknown_enum_value_from_database_is_loud(self):
        with self.assertRaises(ValueError):
            Device.from_row({"mac_address": "AA:BB:CC:DD:EE:FF",
                             "network_status": "sleeping"})

    def test_user_dict_hides_password_hash(self):
        user = User(username="sam", password_hash="abc123")
        self.assertNotIn("password_hash", user.to_dict())
        self.assertIn("password_hash", user.to_row())

    def test_scan_status_outcome_and_json(self):
        scan = Scan(target_scope="192.168.1.0/24", user_id=1,
                    configuration_used={"timeout": 2})
        self.assertTrue(scan.is_running)
        self.assertIsNone(scan.outcome)
        row = scan.to_row()
        self.assertEqual(json.loads(row["configuration_used"]), {"timeout": 2})
        self.assertEqual(row["status"], "running")
        done = Scan.from_row({**row, "status": "completed", "outcome": "partial"})
        self.assertEqual(done.configuration_used, {"timeout": 2})
        self.assertEqual((done.status, done.outcome),
                         (ScanStatus.COMPLETED, ScanOutcome.PARTIAL))
        self.assertFalse(done.is_running)

    def test_audit_json_fields_and_enum(self):
        entry = AuditLog.from_row({"user_id": 1, "record_type": "device",
                                   "record_id": 3, "action": "update",
                                   "old_value": '{"device_name": "a"}',
                                   "new_value": '{"device_name": "b"}'})
        self.assertEqual(entry.action, AuditAction.UPDATE)
        self.assertEqual(entry.new_value, {"device_name": "b"})

    def test_alert_events_live_in_the_link_table(self):
        setting = AlertSetting(user_id=1, recipient_email="a@b.com",
                               events=[AlertEventName.SCAN_FAILED])
        self.assertNotIn("events", setting.to_row())
        self.assertEqual(setting.to_dict()["events"], ["scan_failed"])
        self.assertEqual(AlertSetting.LINK_TABLE, "alert_setting_event")
        event = AlertEvent.from_row({"event_id": 2, "event_name": "backup_failed"})
        self.assertEqual(event.event_name, AlertEventName.BACKUP_FAILED)

    def test_reference_entities(self):
        vendor = Vendor(vendor_name="Amazon")
        self.assertTrue(vendor.is_active)
        vendor.retire()
        self.assertEqual(vendor.status, RecordStatus.RETIRED)
        prefix = OuiPrefix(oui_prefix="F0:D2:F1", vendor_id=3)
        self.assertEqual(prefix.to_row(), {"oui_prefix": "F0:D2:F1", "vendor_id": 3})
        sig = DeviceSignature.from_row({"device_type_id": 1, "pattern_value": "echo",
                                        "evidence_type": "mdns", "status": "active"})
        self.assertEqual(sig.evidence_type, EvidenceType.MDNS)


if __name__ == "__main__":
    unittest.main()