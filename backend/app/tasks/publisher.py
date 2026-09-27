"""Ordered event pipeline for one task run.

publish() is synchronous and non-blocking so it can be called from streaming
callbacks. A single pump coroutine persists each event (assigning its seq)
and forwards it to the attached subscriber, preserving order.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.events import Event
from app.logging import get_logger
from app.tasks.store import TaskStore

log = get_logger(__name__)

Subscriber = Callable[[Event], Awaitable[None]]


class EventPublisher:
    def __init__(self, task_id: str, store: TaskStore) -> None:
        self._task_id = task_id
        self._store = store
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._subscriber: Subscriber | None = None
        self._pump: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._pump is None:
            self._pump = asyncio.create_task(self._run(), name=f"events:{self._task_id}")

    def set_subscriber(self, subscriber: Subscriber | None) -> None:
        self._subscriber = subscriber

    def publish(self, type: str, **fields: Any) -> None:
        self._queue.put_nowait(Event(type=type, task_id=self._task_id, **fields))

    async def close(self) -> None:
        """Flush everything published so far, then stop the pump."""
        if self._pump is None:
            return
        self._queue.put_nowait(None)
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump
        self._pump = None

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            try:
                stored = await self._store.append_event(event)
            except Exception:
                log.exception("event_persist_failed", task_id=self._task_id, type=event.type)
                continue
            subscriber = self._subscriber
            if subscriber is None:
                continue
            try:
                await subscriber(stored)
            except Exception as exc:
                # The client reconnects and replays from the store.
                log.warning("event_delivery_failed", task_id=self._task_id, error=str(exc))
