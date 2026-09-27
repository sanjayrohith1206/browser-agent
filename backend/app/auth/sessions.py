"""Sign-in sessions: opaque bearer tokens, stored hashed in the shared
key-value store (Redis in production) with a fixed lifetime."""

from __future__ import annotations

import hashlib
import secrets

from app.kv import KeyValue


def _key(token: str) -> str:
    return "session:" + hashlib.sha256(token.encode()).hexdigest()


class SessionManager:
    def __init__(self, kv: KeyValue, ttl_hours: int) -> None:
        self._kv = kv
        self._ttl = ttl_hours * 3600

    async def create(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        await self._kv.set(_key(token), user_id, self._ttl)
        return token

    async def resolve(self, token: str) -> str | None:
        if not token or len(token) > 128:
            return None
        return await self._kv.get(_key(token))

    async def revoke(self, token: str) -> None:
        await self._kv.delete(_key(token))
