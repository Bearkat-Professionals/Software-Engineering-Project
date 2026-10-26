"""Unit tests for BB-9 Backup Media. Every test uses its own temporary folder,
so your real backups/ folder is never touched."""
import tempfile
import unittest
from pathlib import Path

from backend.black_boxes.backup_media import BackupMedia, BackupMediaError


class BackupMediaTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name) / "backups"           # doesn't exist yet: save() creates it
        self.media = BackupMedia(self.folder)

    def test_save_and_load_round_trip(self):
        data = {"rows": [1, 2, 3], "text": "caf\u00e9 \u4e2d"}
        stored = self.media.save("full-1", data)
        self.assertEqual(stored.name, "full-1")
        self.assertEqual(self.media.load("full-1"), data)
        self.assertEqual([backup.name for backup in self.media.list()], ["full-1"])

    def test_never_overwrites_an_existing_backup(self):
        self.media.save("full-1", {"first": True})
        with self.assertRaises(BackupMediaError):
            self.media.save("full-1", {"first": False})
        self.assertEqual(self.media.load("full-1"), {"first": True})

    def test_names_that_could_escape_the_folder_are_rejected(self):
        for name in ("../outside", "a/b", "a\\b", "", ".hidden", "a..b"):
            with self.subTest(name=name):
                with self.assertRaises(BackupMediaError):
                    self.media.save(name, {})

    def test_missing_and_damaged_files_are_reported(self):
        with self.assertRaises(BackupMediaError):
            self.media.load("never-saved")

        self.media.save("full-1", {"rows": list(range(100))})
        path = self.folder / "full-1.json.gz"
        path.write_bytes(path.read_bytes()[:20])                # cut the file short
        with self.assertRaises(BackupMediaError):
            self.media.load("full-1")

        path.write_bytes(b"this is not gzip")
        with self.assertRaises(BackupMediaError):
            self.media.load("full-1")

    def test_no_temporary_files_are_left_behind(self):
        self.media.save("full-1", {"a": 1})
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["full-1.json.gz"])

    def test_empty_or_missing_folder_lists_nothing(self):
        self.assertEqual(self.media.list(), [])


if __name__ == "__main__":
    unittest.main()
