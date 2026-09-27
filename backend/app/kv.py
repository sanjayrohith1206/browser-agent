"""Shared short-lived state: sign-in sessions, task locks and rate-limit
counters.

RedisKV keeps it in Redis, so several backend instances share it and it
survives restarts. MemoryKV keeps it in this process: fine for a single
local instance, but sign-ins end when the backend restarts.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol


class KeyValue(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def hit(self, key: str, window_seconds: int) -> int:
        """Count one event in the current fixed window; returns the count."""
        ...

    async def acquire(self, key: str, owner: str, ttl_seconds: int) -> bool:
        """Take a lock unless someone else holds it."""
        ...

    async def refresh(self, key: str, owner: str, ttl_seconds: int) -> bool: ...

    async def release(self, key: str, owner: str) -> None: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class MemoryKV:
    def __init__(self, clock: Any = time.monotonic) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self._clock = clock

    def _live(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires <= self._clock():
            del self._data[key]
            return None
        return value

    def _sweep(self) -> None:
        if len(self._data) > 10_000:
            now = self._clock()
            for k in [k for k, (_, exp) in self._data.items() if exp <= now]:
                del self._data[k]

    async def get(self, key: str) -> str | None:
        async with self._lock:
            return self._live(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        async with self._lock:
            self._sweep()
            self._data[key] = (value, self._clock() + ttl_seconds)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def hit(self, key: str, window_seconds: int) -> int:
        async with self._lock:
            current = self._live(key)
            if current is None:
                self._sweep()
                self._data[key] = ("1", self._clock() + window_seconds)
                return 1
            count = int(current) + 1
            self._data[key] = (str(count), self._data[key][1])
            return count

    async def acquire(self, key: str, owner: str, ttl_seconds: int) -> bool:
        async with self._lock:
            holder = self._live(key)
            if holder is not None and holder != owner:
                return False
            self._data[key] = (owner, self._clock() + ttl_seconds)
            return True

    async def refresh(self, key: str, owner: str, ttl_seconds: int) -> bool:
        async with self._lock:
            if self._live(key) != owner:
                return False
            self._data[key] = (owner, self._clock() + ttl_seconds)
            return True

    async def release(self, key: str, owner: str) -> None:
        async with self._lock:
            if self._live(key) == owner:
                del self._data[key]

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


# Compare-and-set scripts: only the lock's owner may extend or release it.
_REFRESH = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_HIT = """
local n = redis.call('incr', KEYS[1])
if n == 1 then redis.call('expire', KEYS[1], ARGV[1]) end
return n
"""


class RedisKV:
    def __init__(self, client: Any, prefix: str = "browser-agent:") -> None:
        self._redis = client
        self._prefix = prefix

    @classmethod
    def from_url(cls, url: str) -> RedisKV:
        from redis.asyncio import Redis

        return cls(Redis.from_url(url, decode_responses=True, health_check_interval=30))

    def _k(self, key: str) -> str:
        return self._prefix + key

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(self._k(key))
        return None if value is None else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(self._k(key), value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._redis.delete(self._k(key))

    async def hit(self, key: str, window_seconds: int) -> int:
        return int(await self._redis.eval(_HIT, 1, self._k(key), window_seconds))

    async def acquire(self, key: str, owner: str, ttl_seconds: int) -> bool:
        if await self._redis.set(self._k(key), owner, ex=ttl_seconds, nx=True):
            return True
        # Re-entrant for the same owner.
        return bool(await self.refresh(key, owner, ttl_seconds))

    async def refresh(self, key: str, owner: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.eval(_REFRESH, 1, self._k(key), owner, ttl_seconds))

    async def release(self, key: str, owner: str) -> None:
        await self._redis.eval(_RELEASE, 1, self._k(key), owner)

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._redis.aclose()
