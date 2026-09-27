"""Task persistence.

TaskStore is the persistence boundary; SqlTaskStore implements it over
PostgreSQL (or SQLite in development) with sensitive columns encrypted.

Events are numbered in order per task. Streaming fragments
(agent_message_delta / agent_message_reset) are numbered but not stored:
the complete agent_message that follows them is, so a replay shows the
same conversation without the token-by-token noise.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, messages_from_dict, messages_to_dict
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent.events import TERMINAL_STATUSES, Event, TaskStatus, utcnow
from app.db import schema as t
from app.db.crypto import Cipher
from app.tasks.models import PageContext, TabInfo, Task, TaskStep, TaskSummary
from app.tools.types import ToolError

TRANSIENT_EVENTS = frozenset({"agent_message_delta", "agent_message_reset"})
UNFINISHED_STATUSES: tuple[TaskStatus, ...] = ("queued", "running", "waiting")


class TaskNotFoundError(KeyError):
    pass


class TaskStore(Protocol):
    async def create(self, task: Task, user_id: str) -> Task: ...

    async def get(self, task_id: str, user_id: str | None = None) -> Task:
        """The task with its steps. With user_id, only if that user owns it."""
        ...

    async def owner(self, task_id: str) -> str: ...

    async def recent(
        self, user_id: str, limit: int = 50, before: datetime | None = None
    ) -> list[TaskSummary]: ...

    async def update(self, task_id: str, **changes: Any) -> None: ...

    async def delete(self, task_id: str, user_id: str) -> None: ...

    async def running_count(self, user_id: str) -> int: ...

    async def add_step(self, task_id: str, step: TaskStep) -> None: ...

    async def finish_step(
        self,
        task_id: str,
        call_id: str,
        *,
        success: bool,
        message: str,
        error: ToolError | None,
    ) -> None: ...

    async def append_event(self, event: Event) -> Event: ...

    async def events(self, task_id: str, after_seq: int = 0) -> list[Event]: ...

    async def save_messages(self, task_id: str, start: int, messages: list[BaseMessage]) -> None:
        """Store the agent conversation from position `start` on."""
        ...

    async def messages(self, task_id: str) -> list[BaseMessage]: ...

    def forget(self, task_id: str) -> None:
        """Drop per-task caches once a run has ended."""
        ...

    async def interrupt_unfinished(self) -> int: ...

    async def purge_before(self, cutoff: datetime) -> int: ...


_UPDATABLE = frozenset(
    {"status", "result", "error", "outcome", "current_url", "current_tab", "tabs"}
)


class SqlTaskStore:
    def __init__(self, engine: AsyncEngine, cipher: Cipher) -> None:
        self._engine = engine
        self._cipher = cipher
        self._next_seq: dict[str, int] = {}
        self._seq_lock = asyncio.Lock()

    # ------------------------------------------------------------ tasks

    async def create(self, task: Task, user_id: str) -> Task:
        c = self._cipher
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(t.tasks).values(
                    id=task.id,
                    user_id=user_id,
                    status=task.status,
                    outcome=task.outcome,
                    goal_enc=c.encrypt(task.goal),
                    result_enc=c.encrypt_opt(task.result),
                    error=task.error,
                    page_enc=c.encrypt_json(task.page.model_dump()) if task.page else None,
                    current_url_enc=c.encrypt_opt(task.current_url),
                    current_tab=task.current_tab,
                    tabs_enc=c.encrypt_json([tab.model_dump() for tab in task.tabs]),
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
            )
        return task.model_copy(deep=True)

    async def get(self, task_id: str, user_id: str | None = None) -> Task:
        query = select(t.tasks).where(t.tasks.c.id == task_id)
        if user_id is not None:
            query = query.where(t.tasks.c.user_id == user_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
            if row is None:
                raise TaskNotFoundError(task_id)
            steps = (
                await conn.execute(
                    select(t.task_steps)
                    .where(t.task_steps.c.task_id == task_id)
                    .order_by(t.task_steps.c.id)
                )
            ).all()
        return self._task(row, [self._step(s) for s in steps])

    async def owner(self, task_id: str) -> str:
        async with self._engine.connect() as conn:
            user_id = await conn.scalar(select(t.tasks.c.user_id).where(t.tasks.c.id == task_id))
        if user_id is None:
            raise TaskNotFoundError(task_id)
        return str(user_id)

    async def recent(
        self, user_id: str, limit: int = 50, before: datetime | None = None
    ) -> list[TaskSummary]:
        query = (
            select(t.tasks)
            .where(t.tasks.c.user_id == user_id)
            .order_by(t.tasks.c.created_at.desc())
            .limit(limit)
        )
        if before is not None:
            query = query.where(t.tasks.c.created_at < before)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        c = self._cipher
        return [
            TaskSummary(
                id=r.id,
                goal=c.decrypt(r.goal_enc),
                status=r.status,
                outcome=r.outcome,
                current_url=c.decrypt_opt(r.current_url_enc),
                created_at=_utc(r.created_at),
                updated_at=_utc(r.updated_at),
            )
            for r in rows
        ]

    async def update(self, task_id: str, **changes: Any) -> None:
        unknown = set(changes) - _UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        c = self._cipher
        values: dict[str, Any] = {"updated_at": utcnow()}
        for key, value in changes.items():
            match key:
                case "result":
                    values["result_enc"] = c.encrypt_opt(value)
                case "current_url":
                    values["current_url_enc"] = c.encrypt_opt(value)
                case "tabs":
                    values["tabs_enc"] = c.encrypt_json(
                        [tab.model_dump() if isinstance(tab, TabInfo) else tab for tab in value]
                    )
                case _:
                    values[key] = value
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(t.tasks).where(t.tasks.c.id == task_id).values(**values)
            )
        if result.rowcount == 0:
            raise TaskNotFoundError(task_id)

    async def delete(self, task_id: str, user_id: str) -> None:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(t.tasks).where(t.tasks.c.id == task_id, t.tasks.c.user_id == user_id)
            )
        if result.rowcount == 0:
            raise TaskNotFoundError(task_id)
        self.forget(task_id)

    async def running_count(self, user_id: str) -> int:
        async with self._engine.connect() as conn:
            count = await conn.scalar(
                select(func.count())
                .select_from(t.tasks)
                .where(t.tasks.c.user_id == user_id, t.tasks.c.status.in_(("running", "waiting")))
            )
        return int(count or 0)

    # ------------------------------------------------------------ steps

    async def add_step(self, task_id: str, step: TaskStep) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(t.task_steps).values(
                    task_id=task_id,
                    call_id=step.call_id,
                    tool=step.tool,
                    input_enc=self._cipher.encrypt_json(step.input),
                    tab_id=step.tab_id,
                    started_at=step.started_at,
                )
            )

    async def finish_step(
        self,
        task_id: str,
        call_id: str,
        *,
        success: bool,
        message: str,
        error: ToolError | None,
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                update(t.task_steps)
                .where(t.task_steps.c.task_id == task_id, t.task_steps.c.call_id == call_id)
                .values(
                    success=success,
                    message_enc=self._cipher.encrypt(message),
                    error_code=error.code if error else None,
                    error_message_enc=self._cipher.encrypt(error.message) if error else None,
                    finished_at=utcnow(),
                )
            )

    # ----------------------------------------------------------- events

    async def append_event(self, event: Event) -> Event:
        async with self._seq_lock:
            seq = self._next_seq.get(event.task_id)
            if seq is None:
                async with self._engine.connect() as conn:
                    exists = await conn.scalar(
                        select(t.tasks.c.id).where(t.tasks.c.id == event.task_id)
                    )
                    if exists is None:
                        raise TaskNotFoundError(event.task_id)
                    last = await conn.scalar(
                        select(func.max(t.task_events.c.seq)).where(
                            t.task_events.c.task_id == event.task_id
                        )
                    )
                seq = int(last or 0) + 1
            self._next_seq[event.task_id] = seq + 1
        stored = event.model_copy(update={"seq": seq})
        if event.type not in TRANSIENT_EVENTS:
            async with self._engine.begin() as conn:
                await conn.execute(
                    insert(t.task_events).values(
                        task_id=event.task_id,
                        seq=seq,
                        type=event.type,
                        payload_enc=self._cipher.encrypt_json(stored.wire()),
                        created_at=stored.timestamp,
                    )
                )
        return stored

    async def events(self, task_id: str, after_seq: int = 0) -> list[Event]:
        async with self._engine.connect() as conn:
            if await conn.scalar(select(t.tasks.c.id).where(t.tasks.c.id == task_id)) is None:
                raise TaskNotFoundError(task_id)
            rows = (
                await conn.execute(
                    select(t.task_events.c.payload_enc)
                    .where(t.task_events.c.task_id == task_id, t.task_events.c.seq > after_seq)
                    .order_by(t.task_events.c.seq)
                )
            ).all()
        return [Event.model_validate(self._cipher.decrypt_json(r.payload_enc)) for r in rows]

    # --------------------------------------------------------- messages

    async def save_messages(self, task_id: str, start: int, messages: list[BaseMessage]) -> None:
        if not messages:
            return
        now = utcnow()
        rows = [
            {
                "task_id": task_id,
                "position": start + i,
                "role": message.type,
                "content_enc": self._cipher.encrypt_json(messages_to_dict([_storable(message)])[0]),
                "created_at": now,
            }
            for i, message in enumerate(messages)
        ]
        async with self._engine.begin() as conn:
            await conn.execute(insert(t.agent_messages), rows)

    async def messages(self, task_id: str) -> list[BaseMessage]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(t.agent_messages.c.content_enc)
                    .where(t.agent_messages.c.task_id == task_id)
                    .order_by(t.agent_messages.c.position)
                )
            ).all()
        return messages_from_dict([self._cipher.decrypt_json(r.content_enc) for r in rows])

    # ------------------------------------------------------ maintenance

    def forget(self, task_id: str) -> None:
        self._next_seq.pop(task_id, None)

    async def interrupt_unfinished(self) -> int:
        """Mark tasks that were in progress when the server stopped."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(t.tasks)
                .where(t.tasks.c.status.in_(UNFINISHED_STATUSES))
                .values(
                    status="interrupted",
                    error="The server restarted before the task finished.",
                    updated_at=utcnow(),
                )
            )
        return int(result.rowcount or 0)

    async def purge_before(self, cutoff: datetime) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(t.tasks).where(
                    t.tasks.c.created_at < cutoff,
                    t.tasks.c.status.in_(tuple(TERMINAL_STATUSES)),
                )
            )
        return int(result.rowcount or 0)

    # ---------------------------------------------------------- mapping

    def _task(self, row: Row[Any], steps: list[TaskStep]) -> Task:
        c = self._cipher
        page = c.decrypt_json(row.page_enc) if row.page_enc else None
        tabs = c.decrypt_json(row.tabs_enc) if row.tabs_enc else []
        return Task(
            id=row.id,
            goal=c.decrypt(row.goal_enc),
            status=row.status,
            outcome=row.outcome,
            page=PageContext.model_validate(page) if page else None,
            current_url=c.decrypt_opt(row.current_url_enc),
            current_tab=row.current_tab,
            tabs=[TabInfo.model_validate(tab) for tab in tabs],
            steps=steps,
            result=c.decrypt_opt(row.result_enc),
            error=row.error,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )

    def _step(self, row: Row[Any]) -> TaskStep:
        c = self._cipher
        error = (
            ToolError(code=row.error_code, message=c.decrypt(row.error_message_enc))
            if row.error_code and row.error_message_enc
            else None
        )
        return TaskStep(
            call_id=row.call_id,
            tool=row.tool,
            input=c.decrypt_json(row.input_enc),
            tab_id=row.tab_id,
            success=row.success,
            message=c.decrypt_opt(row.message_enc),
            error=error,
            started_at=_utc(row.started_at),
            finished_at=_utc(row.finished_at) if row.finished_at else None,
        )


def _utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything is stored in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _storable(message: BaseMessage) -> BaseMessage:
    """Screenshots are never stored: image blocks become a placeholder."""
    if not isinstance(message, HumanMessage) or isinstance(message.content, str):
        return message
    content = [
        {"type": "text", "text": "[screenshot not stored]"}
        if isinstance(block, dict) and block.get("type") in ("image", "image_url")
        else block
        for block in message.content
    ]
    return message.model_copy(update={"content": content})
