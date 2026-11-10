"""Unit tests for BB-5 Password Hashing (#87). No database, no network.

    python -m unittest tests.black_boxes.test_password_hashing
"""
from __future__ import annotations

import unittest

from backend.black_boxes.password_hashing import hash_password, verify_password

PASSWORD = "correct horse battery staple"


#Hashing
class TestHashPassword(unittest.TestCase):
    def test_hash_is_argon2id_and_hides_the_password(self):
        stored = hash_password(PASSWORD)
        self.assertTrue(stored.startswith("$argon2id$"), stored)
        self.assertNotIn(PASSWORD, stored)

    def test_hash_fits_the_column(self):
        self.assertLessEqual(len(hash_password(PASSWORD)), 255)       # VARCHAR(255)

    def test_same_password_twice_gives_different_hashes(self):
        first, second = hash_password(PASSWORD), hash_password(PASSWORD)
        self.assertNotEqual(first, second)                            # a new salt each time
        self.assertTrue(verify_password(first, PASSWORD))
        self.assertTrue(verify_password(second, PASSWORD))

    def test_empty_password_raises(self):
        with self.assertRaises(ValueError):
            hash_password("")

    def test_long_and_non_english_passwords_work(self):
        for password in ("x" * 128, "pässwörd🔒"):
            with self.subTest(password=password[:12]):
                stored = hash_password(password)
                self.assertTrue(verify_password(stored, password))
                self.assertFalse(verify_password(stored, password + "!"))


#Verifying
class TestVerifyPassword(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stored = hash_password(PASSWORD)

    def test_right_password(self):
        self.assertTrue(verify_password(self.stored, PASSWORD))

    def test_wrong_password_is_false(self):
        for wrong in ("wrong", PASSWORD.upper(), PASSWORD + " ", PASSWORD[:-1]):
            with self.subTest(wrong=wrong):
                self.assertFalse(verify_password(self.stored, wrong))

    def test_empty_password_is_false(self):
        self.assertFalse(verify_password(self.stored, ""))

    def test_text_that_cannot_be_utf8_is_false(self):
        # A sign-in request can send {"password": "\ud800"}; it must be a wrong password, not a 500
        self.assertFalse(verify_password(self.stored, "\ud800"))

    def test_bad_hashes_are_false_not_errors(self):
        bad_hashes = {
            "empty": "",
            "cut off": self.stored[: len(self.stored) // 2],
            "made up": "$argon2id$v=19$m=65536,t=3,p=4$notasalt$notahash",
            "plain text": PASSWORD,
            "bcrypt": "$2b$12$abcdefghijklmnopqrstuuJw5GmRoS9WVJcQ1EGJUSyuH7zeu6Jyi",
            "argon2i, not argon2id": self.stored.replace("$argon2id$", "$argon2i$", 1),
            "not text": None,
        }
        for name, stored in bad_hashes.items():
            with self.subTest(name):
                self.assertFalse(verify_password(stored, PASSWORD))


if __name__ == "__main__":
    unittest.main()