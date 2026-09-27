"""User memory: short notes the user wants the assistant to keep in mind
(preferences, details about them). Users read and edit these themselves;
the agent only reads them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent.events import utcnow
from app.db import schema as t
from app.db.crypto import Cipher

MAX_MEMORY_ITEMS = 100
MAX_MEMORY_CHARS = 500


class MemoryItem(BaseModel):
    id: str
    content: str
    created_at: datetime
    updated_at: datetime


class MemoryNotFoundError(KeyError):
    pass


class MemoryFullError(ValueError):
    pass


class MemoryStore:
    def __init__(self, engine: AsyncEngine, cipher: Cipher) -> None:
        self._engine = engine
        self._cipher = cipher

    async def list(self, user_id: str) -> list[MemoryItem]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(t.memory)
                    .where(t.memory.c.user_id == user_id)
                    .order_by(t.memory.c.created_at, t.memory.c.id)
                )
            ).all()
        return [
            MemoryItem(
                id=r.id,
                content=self._cipher.decrypt(r.content_enc),
                created_at=_utc(r.created_at),
                updated_at=_utc(r.updated_at),
            )
            for r in rows
        ]

    async def add(self, user_id: str, content: str) -> MemoryItem:
        now = utcnow()
        item = MemoryItem(id=str(uuid.uuid4()), content=content, created_at=now, updated_at=now)
        async with self._engine.begin() as conn:
            count = await conn.scalar(
                select(func.count()).select_from(t.memory).where(t.memory.c.user_id == user_id)
            )
            if int(count or 0) >= MAX_MEMORY_ITEMS:
                raise MemoryFullError(user_id)
            await conn.execute(
                insert(t.memory).values(
                    id=item.id,
                    user_id=user_id,
                    content_enc=self._cipher.encrypt(content),
                    created_at=now,
                    updated_at=now,
                )
            )
        return item

    async def update(self, user_id: str, item_id: str, content: str) -> MemoryItem:
        now = utcnow()
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(t.memory)
                .where(t.memory.c.id == item_id, t.memory.c.user_id == user_id)
                .values(content_enc=self._cipher.encrypt(content), updated_at=now)
            )
            if result.rowcount == 0:
                raise MemoryNotFoundError(item_id)
            created = await conn.scalar(
                select(t.memory.c.created_at).where(t.memory.c.id == item_id)
            )
        assert created is not None
        return MemoryItem(id=item_id, content=content, created_at=_utc(created), updated_at=now)

    async def delete(self, user_id: str, item_id: str) -> None:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(t.memory).where(t.memory.c.id == item_id, t.memory.c.user_id == user_id)
            )
        if result.rowcount == 0:
            raise MemoryNotFoundError(item_id)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
