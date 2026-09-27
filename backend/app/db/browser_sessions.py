"""A log of browser sessions: each time an extension panel attaches to a
task over its WebSocket, and when and why it left."""

from __future__ import annotations

import uuid

from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent.events import utcnow
from app.db import schema as t


class BrowserSessionLog:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def opened(self, task_id: str, user_id: str, client: str) -> str:
        session_id = str(uuid.uuid4())
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(t.browser_sessions).values(
                    id=session_id,
                    task_id=task_id,
                    user_id=user_id,
                    client=client[:200],
                    connected_at=utcnow(),
                )
            )
        return session_id

    async def closed(self, session_id: str, reason: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(t.browser_sessions)
                .where(t.browser_sessions.c.id == session_id)
                .values(disconnected_at=utcnow(), close_reason=reason[:40])
            )
