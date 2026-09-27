"""The shared key-value store, in memory and on Redis (fakeredis; set
TEST_REDIS_URL to also run against a real server)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis

from app.kv import KeyValue, MemoryKV, RedisKV


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(params=["memory", "fakeredis", "redis"])
async def kv(request: pytest.FixtureRequest) -> AsyncIterator[KeyValue]:
    if request.param == "memory":
        yield MemoryKV()
    elif request.param == "fakeredis":
        store = RedisKV(FakeAsyncRedis(decode_responses=True))
        yield store
        await store.close()
    else:
        url = os.environ.get("TEST_REDIS_URL")
        if not url:
            pytest.skip("TEST_REDIS_URL not set")
        from redis.asyncio import Redis

        client = Redis.from_url(url, decode_responses=True)
        prefix = f"test-{uuid.uuid4()}:"
        yield RedisKV(client, prefix=prefix)
        keys = [k async for k in client.scan_iter(f"{prefix}*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_values_and_expiry(kv: KeyValue) -> None:
    await kv.set("a", "1", 60)
    assert await kv.get("a") == "1"
    await kv.delete("a")
    assert await kv.get("a") is None
    assert await kv.ping()


async def test_locks(kv: KeyValue) -> None:
    assert await kv.acquire("lock", "me", 60)
    assert await kv.acquire("lock", "me", 60)  # re-entrant
    assert not await kv.acquire("lock", "you", 60)
    assert not await kv.refresh("lock", "you", 60)
    await kv.release("lock", "you")  # not theirs: no effect
    assert not await kv.acquire("lock", "you", 60)
    assert await kv.refresh("lock", "me", 60)
    await kv.release("lock", "me")
    assert await kv.acquire("lock", "you", 60)


async def test_rate_counter(kv: KeyValue) -> None:
    assert [await kv.hit("rl", 60) for _ in range(3)] == [1, 2, 3]


async def test_memory_kv_expires() -> None:
    clock = Clock()
    kv = MemoryKV(clock=clock)
    await kv.set("a", "1", 10)
    await kv.acquire("lock", "me", 10)
    assert await kv.hit("rl", 10) == 1
    clock.now += 11
    assert await kv.get("a") is None
    assert await kv.acquire("lock", "you", 10)  # expired locks can be taken
    assert await kv.hit("rl", 10) == 1  # new window
