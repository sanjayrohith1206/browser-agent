"""Encryption of sensitive data at rest.

Everything a user typed or the agent read (task goals and answers, page
URLs and titles, tool inputs, conversation history, memory) is encrypted
with Fernet (AES-128-CBC + HMAC-SHA256) before it reaches the database.

Keys come from DATA_ENCRYPTION_KEYS, a comma-separated list. The first key
encrypts; every key can decrypt, so rotating means putting a new key first
and keeping the old ones until existing data has been re-encrypted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.logging import get_logger

log = get_logger(__name__)


class EncryptionConfigError(ValueError):
    pass


class Cipher:
    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise EncryptionConfigError("at least one encryption key is required")
        try:
            self._fernet = MultiFernet([Fernet(k.encode()) for k in keys])
        except ValueError as exc:
            raise EncryptionConfigError(
                "DATA_ENCRYPTION_KEYS must be Fernet keys (32 url-safe base64-encoded bytes)"
            ) from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise EncryptionConfigError(
                "stored data could not be decrypted: is DATA_ENCRYPTION_KEYS missing a key?"
            ) from exc

    def encrypt_json(self, value: Any) -> str:
        return self.encrypt(json.dumps(value, separators=(",", ":"), default=str))

    def decrypt_json(self, token: str) -> Any:
        return json.loads(self.decrypt(token))

    def encrypt_opt(self, value: str | None) -> str | None:
        return None if value is None else self.encrypt(value)

    def decrypt_opt(self, token: str | None) -> str | None:
        return None if token is None else self.decrypt(token)


def generate_key() -> str:
    return Fernet.generate_key().decode()


def load_or_create_key_file(path: Path) -> str:
    """Development convenience: a key kept in a local file (mode 0600).
    Production must set DATA_ENCRYPTION_KEYS explicitly."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    log.warning("encryption_key_generated", path=str(path))
    return key
