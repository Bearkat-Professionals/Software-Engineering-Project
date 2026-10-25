"""Unit tests for the backup format and 3.5.3 Verify Backup. No database needed."""
import copy
import json
import unittest

from backend.data_access.backup_recovery.backup_format import Backup, BackupKind, build_backup
from backend.data_access.backup_recovery.verify_backup import check_backup

# A version-1 backup saved exactly as the code wrote it in October 2026.
# If a code change makes this fail, the backup format changed: old backups would
# no longer verify or restore. Either undo the change, or raise FORMAT_VERSION
# and keep accepting version 1 (see backup_format.py).
SAVED_VERSION_1_BACKUP = {
    "manifest": {
        "kind": "partial",
        "created_at": "2026-10-25T14:00:00",
        "database": "network_device_scanner",
        "tables": {
            "vendor": {"row_count": 1,
                       "checksum": "f5375ed3dcf009b082ac25154983987c464c9c248016417ac72d4eb7d03dc57f"},
            "oui_prefix": {"row_count": 0,
                           "checksum": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"},
        },
        "format_version": 1,
    },
    "tables": {
        "vendor": [{"vendor_id": 1, "vendor_name": "unknown", "status": "active"}],
        "oui_prefix": [],
    },
}


class BackupFormatTests(unittest.TestCase):
    def test_round_trip_through_json(self):
        backup = build_backup(BackupKind.FULL, "db", {"vendor": [{"vendor_id": 1}], "scan": []})
        self.assertEqual(Backup.from_dict(json.loads(json.dumps(backup.to_dict()))), backup)

    def test_backups_made_by_older_code_still_verify(self):
        result = check_backup("saved", SAVED_VERSION_1_BACKUP)
        self.assertTrue(result.ok, result.problems)


class VerifyBackupTests(unittest.TestCase):
    def damaged(self, change):
        """A copy of the saved backup with one thing broken, and what Verify says about it."""
        data = copy.deepcopy(SAVED_VERSION_1_BACKUP)
        change(data)
        return check_backup("damaged", data)

    def test_good_backup_passes_and_is_handed_back(self):
        result = check_backup("saved", SAVED_VERSION_1_BACKUP)
        self.assertEqual(result.problems, ())
        self.assertEqual(result.backup.manifest.kind, BackupKind.PARTIAL)

    def test_every_kind_of_damage_is_found(self):
        cases = {
            "a value changed":        lambda d: d["tables"]["vendor"][0].update(vendor_name="evil"),
            "a row added":            lambda d: d["tables"]["vendor"].append({"vendor_id": 2}),
            "a table's rows missing": lambda d: d["tables"].pop("oui_prefix"),
            "an unlisted table":      lambda d: d["tables"].update(extra=[]),
            "unknown format version": lambda d: d["manifest"].update(format_version=99),
            "manifest missing":       lambda d: d.pop("manifest"),
            "rows in the wrong shape": lambda d: d["tables"].update(vendor="not rows"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                result = self.damaged(change)
                self.assertFalse(result.ok)
                self.assertTrue(result.problems)
                self.assertIsNone(result.backup)      # a failed backup can't be restored from


if __name__ == "__main__":
    unittest.main()
