"""BB-9 Backup Media: safe compressed and optionally encrypted backup files.

When ``BACKUP_PUBLIC_KEY`` names an RSA public-key PEM file, new backups are
encrypted with a fresh AES-256-GCM key. That key is wrapped with RSA-OAEP
(SHA-256), so the private key is only needed for verify and restore. Existing
plain ``.json.gz`` backups remain readable for backwards compatibility.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FOLDER = PROJECT_ROOT / "backups"
PLAIN_SUFFIX = ".json.gz"
ENCRYPTED_SUFFIX = ".json.gz.enc"
SUFFIX = PLAIN_SUFFIX
ENCRYPTED_FORMAT = "ndi-backup-encrypted"
ENCRYPTED_VERSION = 1

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def is_safe_name(name: object) -> bool:
    """Return whether *name* is safe to use below the backup directory."""
    return isinstance(name, str) and _SAFE_NAME.fullmatch(name) is not None and ".." not in name


class BackupMediaError(Exception):
    """A backup could not be written, decrypted, or read."""


@dataclass(frozen=True)
class StoredBackup:
    name: str
    size_bytes: int
    modified_at: str
    encrypted: bool = False


class BackupMedia:
    def __init__(self, folder: Path, public_key_path: Path | None = None):
        self._folder = Path(folder)
        self._public_key_path = Path(public_key_path) if public_key_path else None

    @classmethod
    def from_env(cls) -> "BackupMedia":
        public_key = os.getenv("BACKUP_PUBLIC_KEY", "").strip()
        return cls(Path(os.getenv("BACKUP_DIR", DEFAULT_FOLDER)), Path(public_key) if public_key else None)

    @property
    def encryption_enabled(self) -> bool:
        return self._public_key_path is not None

    def save(self, name: str, data: dict[str, Any]) -> StoredBackup:
        """Write a new backup atomically and never overwrite either format."""
        plain_path, encrypted_path = self._paths_for(name)
        if plain_path.exists() or encrypted_path.exists():
            raise BackupMediaError(f"a backup named '{name}' already exists")
        encrypted = self.encryption_enabled
        path = encrypted_path if encrypted else plain_path
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            payload = gzip.compress(json.dumps(data).encode("utf-8"))
            if encrypted:
                payload = self._encrypt(payload)
            handle, temp_name = tempfile.mkstemp(dir=self._folder, suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as raw:
                    raw.write(payload)
                os.replace(temp_name, path)
            except BaseException:
                Path(temp_name).unlink(missing_ok=True)
                raise
        except BackupMediaError:
            raise
        except OSError as exc:
            raise BackupMediaError(f"could not write backup '{name}': {exc}") from exc
        return self._describe(path)

    def load(
        self,
        name: str,
        *,
        private_key_pem: str | bytes | None = None,
        passphrase: str | bytes | None = None,
    ) -> dict[str, Any]:
        """Load either format. Encrypted backups require the matching private key."""
        plain_path, encrypted_path = self._paths_for(name)
        if encrypted_path.exists():
            path, encrypted = encrypted_path, True
        elif plain_path.exists():
            path, encrypted = plain_path, False
        else:
            raise BackupMediaError(f"no backup named '{name}'")
        try:
            payload = path.read_bytes()
            if encrypted:
                payload = self._decrypt(name, payload, private_key_pem, passphrase)
            return json.loads(gzip.decompress(payload).decode("utf-8"))
        except BackupMediaError:
            raise
        except (OSError, EOFError, UnicodeError, ValueError) as exc:
            raise BackupMediaError(f"backup '{name}' is damaged or unreadable") from exc

    def list(self) -> list[StoredBackup]:
        """Return every stored backup, both formats, newest first."""
        if not self._folder.exists():
            return []
        paths = list(self._folder.glob(f"*{PLAIN_SUFFIX}"))
        paths.extend(self._folder.glob(f"*{ENCRYPTED_SUFFIX}"))
        backups = [self._describe(path) for path in paths]
        return sorted(backups, key=lambda backup: backup.modified_at, reverse=True)

    def _encrypt(self, compressed: bytes) -> bytes:
        public_key = self._load_public_key()
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        wrapped_key = public_key.encrypt(
            data_key,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                         algorithm=hashes.SHA256(), label=None),
        )
        header = {
            "format": ENCRYPTED_FORMAT,
            "version": ENCRYPTED_VERSION,
            "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
        }
        header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(data_key).encrypt(nonce, compressed, header_bytes)
        return header_bytes + b"\n" + ciphertext

    def _decrypt(
        self,
        name: str,
        payload: bytes,
        private_key_pem: str | bytes | None,
        passphrase: str | bytes | None,
    ) -> bytes:
        if private_key_pem is None:
            raise BackupMediaError(f"backup '{name}' is encrypted; provide its private key")
        try:
            header_bytes, ciphertext = payload.split(b"\n", 1)
            header = json.loads(header_bytes.decode("utf-8"))
            if header.get("format") != ENCRYPTED_FORMAT or header.get("version") != ENCRYPTED_VERSION:
                raise ValueError("unsupported encrypted backup header")
            wrapped_key = base64.b64decode(header["wrapped_key"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
            if len(nonce) != 12:
                raise ValueError("invalid nonce")
            key_bytes = private_key_pem.encode("utf-8") if isinstance(private_key_pem, str) else private_key_pem
            password = passphrase.encode("utf-8") if isinstance(passphrase, str) else passphrase
            private_key = serialization.load_pem_private_key(key_bytes, password=password)
            if not isinstance(private_key, rsa.RSAPrivateKey):
                raise TypeError("not an RSA private key")
            data_key = private_key.decrypt(
                wrapped_key,
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                             algorithm=hashes.SHA256(), label=None),
            )
            return AESGCM(data_key).decrypt(nonce, ciphertext, header_bytes)
        except BackupMediaError:
            raise
        except (InvalidTag, KeyError, ValueError, TypeError, binascii.Error) as exc:
            raise BackupMediaError(f"backup '{name}' could not be decrypted or is damaged") from exc

    def _load_public_key(self) -> rsa.RSAPublicKey:
        try:
            key = serialization.load_pem_public_key(self._public_key_path.read_bytes())
        except (OSError, ValueError, TypeError) as exc:
            raise BackupMediaError("the configured backup public key could not be read") from exc
        if not isinstance(key, rsa.RSAPublicKey):
            raise BackupMediaError("the configured backup public key is not an RSA key")
        return key

    def _paths_for(self, name: str) -> tuple[Path, Path]:
        if not is_safe_name(name):
            raise BackupMediaError(f"'{name}' is not a valid backup name")
        return (self._folder / f"{name}{PLAIN_SUFFIX}",
                self._folder / f"{name}{ENCRYPTED_SUFFIX}")

    @staticmethod
    def _describe(path: Path) -> StoredBackup:
        encrypted = path.name.endswith(ENCRYPTED_SUFFIX)
        suffix = ENCRYPTED_SUFFIX if encrypted else PLAIN_SUFFIX
        stat = path.stat()
        return StoredBackup(
            name=path.name.removesuffix(suffix),
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            encrypted=encrypted,
        )


def write_key_pair(public_key_path: Path, private_key_path: Path, passphrase: str | bytes) -> None:
    """Generate an RSA-4096 backup key pair, refusing to overwrite either file."""
    public_path, private_path = Path(public_key_path), Path(private_key_path)
    if public_path.exists() or private_path.exists():
        raise BackupMediaError("refusing to overwrite an existing backup key file")
    password = passphrase.encode("utf-8") if isinstance(passphrase, str) else passphrase
    if not password:
        raise BackupMediaError("the private-key passphrase must not be empty")
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    try:
        public_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_bytes(private_bytes)
        try:
            public_path.write_bytes(public_bytes)
        except BaseException:
            private_path.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise BackupMediaError("the backup key pair could not be written") from exc
