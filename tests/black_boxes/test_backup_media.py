"""Unit tests for BB-9 Backup Media. Every test uses its own temporary folder,
so your real backups/ folder is never touched."""
import tempfile
import unittest
from pathlib import Path

from backend.black_boxes.backup_media import BackupMedia, BackupMediaError, write_key_pair


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


class EncryptedBackupMediaTests(unittest.TestCase):
    PASSPHRASE = "correct horse battery staple"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.backups = self.root / "backups"
        self.public = self.root / "backup-public.pem"
        self.private = self.root / "backup-private.pem"
        write_key_pair(self.public, self.private, self.PASSPHRASE)
        self.media = BackupMedia(self.backups, self.public)

    def test_encrypted_round_trip_and_listing(self):
        data = {"secret": "password hash", "rows": [1, 2, 3]}
        stored = self.media.save("full-1", data)
        path = self.backups / "full-1.json.gz.enc"

        self.assertTrue(stored.encrypted)
        self.assertTrue(path.exists())
        self.assertNotIn(b"password hash", path.read_bytes())
        with self.assertRaises(BackupMediaError):
            self.media.load("full-1")
        self.assertEqual(self.media.load("full-1", private_key_pem=self.private.read_bytes(),
                                         passphrase=self.PASSPHRASE), data)
        self.assertEqual([(item.name, item.encrypted) for item in self.media.list()], [("full-1", True)])

    def test_wrong_key_and_passphrase_are_refused_without_leaking_them(self):
        self.media.save("full-1", {"a": 1})
        other_public = self.root / "other-public.pem"
        other_private = self.root / "other-private.pem"
        write_key_pair(other_public, other_private, "other secret")

        attempts = ((self.private.read_bytes(), "wrong passphrase"),
                    (other_private.read_bytes(), "other secret"))
        for key, passphrase in attempts:
            with self.subTest(passphrase=passphrase):
                with self.assertRaises(BackupMediaError) as raised:
                    self.media.load("full-1", private_key_pem=key, passphrase=passphrase)
                message = str(raised.exception)
                self.assertNotIn(passphrase, message)
                self.assertNotIn("PRIVATE KEY", message)

    def test_altered_ciphertext_and_header_are_refused(self):
        key = self.private.read_bytes()
        self.media.save("ciphertext", {"a": 1})
        ciphertext_path = self.backups / "ciphertext.json.gz.enc"
        altered = bytearray(ciphertext_path.read_bytes())
        altered[-1] ^= 1
        ciphertext_path.write_bytes(altered)
        with self.assertRaises(BackupMediaError):
            self.media.load("ciphertext", private_key_pem=key, passphrase=self.PASSPHRASE)

        self.media.save("header", {"a": 1})
        header_path = self.backups / "header.json.gz.enc"
        header, ciphertext = header_path.read_bytes().split(b"\n", 1)
        header_path.write_bytes(header.replace(b"ndi-backup-encrypted", b"ndi-backup-tampered")
                                + b"\n" + ciphertext)
        with self.assertRaises(BackupMediaError):
            self.media.load("header", private_key_pem=key, passphrase=self.PASSPHRASE)

    def test_plain_backups_still_work_without_a_public_key(self):
        plain = BackupMedia(self.backups)
        stored = plain.save("plain", {"a": 1})
        self.assertFalse(stored.encrypted)
        self.assertEqual(plain.load("plain"), {"a": 1})

    def test_key_generation_refuses_to_overwrite(self):
        with self.assertRaises(BackupMediaError):
            write_key_pair(self.public, self.private, self.PASSPHRASE)


if __name__ == "__main__":
    unittest.main()
