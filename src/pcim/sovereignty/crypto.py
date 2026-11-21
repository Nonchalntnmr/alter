"""Envelope encryption with per-record data keys.

Every memory item and every interaction gets its own 256-bit data key (DEK). The DEK is
stored only in wrapped form, encrypted under the master key (KEK). Ciphertexts are bound
to their record id through AES-GCM associated data, so a ciphertext copied into another
row fails to decrypt instead of silently becoming that row's content.

Deleting a record deletes its wrapped DEK. With SQLite's secure_delete on and the WAL
checkpointed (store.py does both), no copy of the key survives in the database file, so
any stray ciphertext — in a backup, a log, a crash dump — is unrecoverable.

Limits, stated plainly:
- A backup taken *before* deletion still holds the wrapped DEK. Anyone holding both that
  backup and the KEK can recover the record. Full erasure across backups needs KEK
  rotation (re-wrap all live DEKs under a new KEK, destroy the old one).
- The KEK lives in an env var or a 0600 file. On a shared or compromised machine that is
  not a secret. A platform keychain (macOS Keychain, Secure Enclave) is the 90-day item.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE = 12
_WRAP_AAD = b"pcim/dek/v1"


class MasterKeyMissing(RuntimeError):
    """The store exists but its master key does not. A new key could open none of its records."""


class KeyRing:
    def __init__(self, kek: bytes):
        if len(kek) != 32:
            raise ValueError("master key must be 32 bytes")
        self._kek = AESGCM(kek)

    @classmethod
    def load_or_create(cls, path: Path, env_var: str = "PCIM_MASTER_KEY", store: Path | None = None) -> "KeyRing":
        """The key at `path`, else a new one. `store`: the database this key protects. When it
        already holds data and the key file is gone, no key is made: every record is wrapped under
        the lost key, and anything written under a new one would be unreadable once the original
        is restored from a backup (a store with two keys and no record of which is which)."""
        env = os.environ.get(env_var)
        if env:
            return cls(base64.urlsafe_b64decode(env))
        path = Path(path)
        if path.exists():
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                raise PermissionError(
                    f"{path} is readable by other users (mode {oct(mode)}); run chmod 600"
                )
            return cls(base64.urlsafe_b64decode(path.read_bytes().strip()))
        if store is not None and Path(store).is_file() and Path(store).stat().st_size > 0:
            raise MasterKeyMissing(
                f"{path} is missing, but {store} holds records encrypted under it. Restore master.key from "
                f"your backup into {path.parent} (or set {env_var}). No new key was made: it could open "
                "nothing stored so far, and what it wrote would be unreadable once the original is back.")
        path.parent.mkdir(parents=True, exist_ok=True)
        key = AESGCM.generate_key(bit_length=256)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.urlsafe_b64encode(key))
        return cls(key)

    @classmethod
    def ephemeral(cls) -> "KeyRing":
        """In-memory key for tests and simulations. Nothing written to disk."""
        return cls(AESGCM.generate_key(bit_length=256))

    def new_dek(self) -> tuple[bytes, bytes]:
        dek = AESGCM.generate_key(bit_length=256)
        return dek, self.wrap(dek)

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._kek.encrypt(nonce, dek, _WRAP_AAD)

    def unwrap(self, wrapped: bytes) -> bytes:
        return self._kek.decrypt(wrapped[:_NONCE], wrapped[_NONCE:], _WRAP_AAD)


def seal(dek: bytes, plaintext: bytes, record_id: str) -> bytes:
    nonce = os.urandom(_NONCE)
    return nonce + AESGCM(dek).encrypt(nonce, plaintext, record_id.encode())


def unseal(dek: bytes, blob: bytes, record_id: str) -> bytes:
    return AESGCM(dek).decrypt(blob[:_NONCE], blob[_NONCE:], record_id.encode())
