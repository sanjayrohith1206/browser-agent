"""Password hashing with scrypt (memory-hard, in the standard library)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

MIN_PASSWORD_CHARS = 10
MAX_PASSWORD_CHARS = 256

# n=2**15, r=8 uses 32 MiB per hash: slow enough to resist guessing, fast
# enough for sign-in.
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32
    )
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked against when an email is unknown, so sign-in takes the same time
# whether or not the account exists.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_CHARS:
        return f"Use at least {MIN_PASSWORD_CHARS} characters."
    if len(password) > MAX_PASSWORD_CHARS:
        return f"Use at most {MAX_PASSWORD_CHARS} characters."
    if password.strip() == "" or len(set(password)) < 4:
        return "Choose a less predictable password."
    return None
