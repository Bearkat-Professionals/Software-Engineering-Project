"""BB-5 Password Hashing (black box, #87).

Hides the hashing library: salted Argon2id from argon2-cffi (system design 11.2, 12;
SEC-02, CP-02). Nothing outside this package imports argon2, so the library can be
swapped without touching the callers.

    stored = hash_password("correct horse")        # "$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>"
    verify_password(stored, "correct horse")       # True
    verify_password(stored, "wrong")               # False

Salting is automatic: every hash gets a new random salt, kept inside the hash string
with the settings, so there's no separate salt column (AT-14).
"""

from __future__ import annotations
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

ARGON2ID_PREFIX = "$argon2id$"
_hasher = PasswordHasher()          # the library's default settings, Argon2id

def hash_password(password: str) -> str:
    """A salted Argon2id hash of the password, for app_user.password_hash."""
    if not isinstance(password, str):
        raise TypeError("password must be text")
    if not password:
        raise ValueError("password must not be empty")
    return _hasher.hash(password)

def verify_password(stored_hash: str, password: str) -> bool:
    """True only when the password matches the stored Argon2id hash. Never raises.

    A wrong password, an empty or damaged hash, or a hash from another algorithm
    (bcrypt, or Argon2i/Argon2d) all give False.
    """
    if not isinstance(stored_hash, str) or not stored_hash.startswith(ARGON2ID_PREFIX):
        return False
    if not isinstance(password, str) or not password:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (Argon2Error, ValueError):       # wrong password, damaged hash, or text that can't be UTF-8
        return False