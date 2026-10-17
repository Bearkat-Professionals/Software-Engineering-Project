"""Checks every entity against the database teammate's relational schema.

EXPECTED_COLUMNS is copied from "Network Device Identification Tool -
Relationship Schema". When the schema changes, update this table and the
matching entity class; the test fails until both agree. It needs no database.
ALERT_SETTING_EVENT (alert_setting_id, event_id) is the link table behind
AlertSetting.events and has no entity.
"""
import unittest

from backend.records import ALL_ENTITIES

EXPECTED_COLUMNS = {
    "app_user": ["user_id", "username", "password_hash", "role", "account_status"],
    "device_type": ["device_type_id", "category_name", "status"],
    "vendor": ["vendor_id", "vendor_name", "status"],
    "oui_prefix": ["oui_id", "oui_prefix", "vendor_id"],
    "device": ["device_id", "mac_address", "is_randomized_mac", "current_ip",
               "hostname", "device_type_id", "vendor_id", "device_name", "model",
               "trusted_status", "flagged_status", "confidence_score",
               "first_seen", "last_seen", "network_status"],
    "scan": ["scan_id", "user_id", "target_scope", "configuration_used",
             "started_at", "ended_at", "status", "outcome", "error_message"],
    "scan_observation": ["observation_id", "scan_id", "device_id", "ip_address",
                         "hostname", "status", "observed_at"],
    "device_signature": ["signature_id", "device_type_id", "pattern_value",
                         "evidence_type", "resulting_device_name",
                         "resulting_model", "status"],
    "identification_evidence": ["evidence_id", "device_id", "signature_id",
                                "evidence_type", "raw_value", "weight"],
    "alert_event": ["event_id", "event_name"],
    "alert_setting": ["alert_setting_id", "user_id", "recipient_email", "enabled"],
    "audit_log": ["audit_log_id", "user_id", "record_type", "record_id", "action",
                  "old_value", "new_value", "changed_at"],
}


class SchemaContractTests(unittest.TestCase):
    def test_every_table_has_exactly_one_entity(self):
        tables = sorted(e.TABLE for e in ALL_ENTITIES)
        self.assertEqual(tables, sorted(EXPECTED_COLUMNS))

    def test_entity_attributes_match_columns(self):
        for entity in ALL_ENTITIES:
            with self.subTest(table=entity.TABLE):
                self.assertEqual(sorted(entity.column_names()),
                                 sorted(EXPECTED_COLUMNS[entity.TABLE]))

    def test_primary_key_is_a_column(self):
        for entity in ALL_ENTITIES:
            with self.subTest(table=entity.TABLE):
                self.assertIn(entity.PK, EXPECTED_COLUMNS[entity.TABLE])


if __name__ == "__main__":
    unittest.main()