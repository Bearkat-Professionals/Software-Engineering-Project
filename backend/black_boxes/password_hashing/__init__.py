"""BB-5 Password Hashing (black box).

Hides the hashing library (argon2-cffi). Callers import only from this package:

    from backend.black_boxes.password_hashing import hash_password, verify_password
"""
from backend.black_boxes.password_hashing.password_hashing import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]