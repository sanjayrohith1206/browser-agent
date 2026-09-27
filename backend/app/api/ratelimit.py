"""Fixed-window rate limits over the shared key-value store."""

from __future__ import annotations

from app.kv import KeyValue


class RateLimitedError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, kv: KeyValue) -> None:
        self._kv = kv

    async def check(self, key: str, limit: int, window_seconds: int) -> None:
        """Count one request; raise RateLimitedError past `limit` per window."""
        if await self._kv.hit(f"ratelimit:{key}", window_seconds) > limit:
            raise RateLimitedError(window_seconds)
