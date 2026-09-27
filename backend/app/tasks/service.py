"""Task lifecycle: creation, running the agent, stopping, and streaming.

A task runs while a browser session (the extension's WebSocket) is attached,
because every browser tool executes in that session. If the session drops
mid-run the task is marked interrupted.

Every operation is scoped to the task's owner. Only one browser session can
be attached to a task at a time, across all backend instances: attaching
takes a lock in the shared key-value store and keeps it alive while the
session lasts.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.agent.events import TERMINAL_STATUSES, Event, TaskOutcome, TaskStatus, utcnow
from app.agent.graph import (
    AgentContext,
    AgentState,
    ToolLimits,
    build_graph,
    recursion_limit,
)
from app.agent.interaction import UserInteraction
from app.agent.narration import describe_call
from app.agent.policy import ConfirmationPolicy, DomainPolicy
from app.agent.prompts import task_context
from app.agent.tabs import TabState
from app.browser.bridge import BrowserBridge
from app.config import Settings
from app.db.browser_sessions import BrowserSessionLog
from app.db.memory import MemoryStore
from app.kv import KeyValue
from app.llm.errors import error_status, is_quota_exhausted, is_transient
from app.llm.provider import LLMProvider, StopReason, message_text
from app.logging import get_logger
from app.tasks.models import CreateTaskRequest, Task, TaskStep, TaskSummary
from app.tasks.publisher import EventPublisher, Subscriber
from app.tasks.store import TaskNotFoundError, TaskStore
from app.tools.registry import ToolRegistry
from app.tools.types import ToolResult

log = get_logger(__name__)

SESSION_LOCK_TTL_SECONDS = 60
SESSION_LOCK_REFRESH_SECONDS = 20


class SessionAlreadyAttachedError(RuntimeError):
    pass


class TooManyTasksError(RuntimeError):
    pass


class TaskStillRunningError(RuntimeError):
    pass


@dataclass
class _Run:
    task_id: str
    user_id: str
    publisher: EventPublisher
    bridge: BrowserBridge
    interaction: UserInteraction
    job: asyncio.Task[None] | None = None
    cancel_reason: TaskStatus | None = None
    # Set once the outcome is known: the run is only recording it, and must
    # not be cancelled half way (the panel closes as soon as it sees the
    # final status).
    finishing: bool = False


@dataclass
class _Session:
    lock_owner: str
    log_id: str | None
    heartbeat: asyncio.Task[None] | None = None


class TaskService:
    def __init__(
        self,
        store: TaskStore,
        provider: LLMProvider,
        registry: ToolRegistry,
        settings: Settings,
        *,
        kv: KeyValue,
        memory: MemoryStore | None = None,
        session_log: BrowserSessionLog | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._registry = registry
        self._settings = settings
        self._kv = kv
        self._memory = memory
        self._session_log = session_log
        self._graph = build_graph()
        self._domains = DomainPolicy(settings.allowed_domains, settings.blocked_domains)
        self._confirmations = ConfirmationPolicy(frozenset(settings.confirm_risk_levels))
        self._instance = uuid.uuid4().hex[:12]
        self._runs: dict[str, _Run] = {}
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- queries

    async def create(self, user_id: str, req: CreateTaskRequest) -> Task:
        if await self._store.running_count(user_id) >= self._settings.max_running_tasks_per_user:
            raise TooManyTasksError(user_id)
        task = Task(
            goal=req.prompt,
            page=req.page,
            current_url=req.page.url if req.page else None,
            current_tab=req.page.tab_id if req.page else None,
            tabs=TabState(req.page.tab_id, req.page.url, req.page.title).snapshot()
            if req.page
            else [],
        )
        task = await self._store.create(task, user_id)
        await self._store.append_event(Event(type="task_status", task_id=task.id, status="queued"))
        log.info("task_created", task_id=task.id, user_id=user_id)
        return task

    async def get(self, task_id: str, user_id: str) -> Task:
        return await self._store.get(task_id, user_id)

    async def recent(
        self, user_id: str, limit: int = 50, before: datetime | None = None
    ) -> list[TaskSummary]:
        return await self._store.recent(user_id, limit, before)

    async def events(self, task_id: str, user_id: str, after_seq: int = 0) -> list[Event]:
        await self._store.get(task_id, user_id)  # ownership
        return await self._store.events(task_id, after_seq)

    async def delete(self, task_id: str, user_id: str) -> None:
        task = await self._store.get(task_id, user_id)
        if task.status not in TERMINAL_STATUSES and task.id in self._runs:
            raise TaskStillRunningError(task_id)
        await self._store.delete(task_id, user_id)

    # ---------------------------------------------------------------- sessions

    async def attach(
        self,
        task_id: str,
        user_id: str,
        send: Subscriber,
        after_seq: int = 0,
        client: str = "",
    ) -> None:
        """Attach a browser session: replay past events, then start the task
        if it has not run yet. Raises TaskNotFoundError (also for other
        users' tasks) or SessionAlreadyAttachedError."""
        async with self._lock:
            task = await self._store.get(task_id, user_id)
            owner = f"{self._instance}:{uuid.uuid4().hex[:8]}"
            if task_id in self._sessions or not await self._kv.acquire(
                _session_key(task_id), owner, SESSION_LOCK_TTL_SECONDS
            ):
                raise SessionAlreadyAttachedError(task_id)
            session = _Session(lock_owner=owner, log_id=None)
            self._sessions[task_id] = session
            try:
                if self._session_log is not None:
                    session.log_id = await self._session_log.opened(task_id, user_id, client)
                session.heartbeat = asyncio.create_task(
                    self._keep_lock(task_id, owner), name=f"session-lock:{task_id}"
                )
                for event in await self._store.events(task_id, after_seq):
                    await send(event)
                if task.status == "queued":
                    await self._start(task, user_id, send)
            except BaseException:
                await self._end_session(task_id, "failed")
                raise

    async def detach(self, task_id: str, reason: str = "disconnected") -> None:
        async with self._lock:
            run = self._runs.get(task_id)
            await self._end_session(task_id, reason)
        if run is not None:
            run.publisher.set_subscriber(None)
            await self._cancel(run, "interrupted")

    async def _end_session(self, task_id: str, reason: str) -> None:
        session = self._sessions.pop(task_id, None)
        if session is None:
            return
        if session.heartbeat is not None:
            session.heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.heartbeat
        await self._kv.release(_session_key(task_id), session.lock_owner)
        if self._session_log is not None and session.log_id is not None:
            try:
                await self._session_log.closed(session.log_id, reason)
            except Exception:
                log.exception("session_log_failed", task_id=task_id)

    async def _keep_lock(self, task_id: str, owner: str) -> None:
        while True:
            await asyncio.sleep(SESSION_LOCK_REFRESH_SECONDS)
            try:
                key = _session_key(task_id)
                if not await self._kv.refresh(key, owner, SESSION_LOCK_TTL_SECONDS):
                    log.warning("session_lock_lost", task_id=task_id)
            except Exception as exc:
                log.warning("session_lock_refresh_failed", task_id=task_id, error=str(exc))

    def submit_tool_result(self, task_id: str, call_id: str, result: ToolResult) -> bool:
        run = self._runs.get(task_id)
        return run is not None and run.bridge.resolve(call_id, result)

    def submit_answer(self, task_id: str, request_id: str, answer: str) -> bool:
        run = self._runs.get(task_id)
        return run is not None and run.interaction.answer(request_id, answer)

    def submit_decision(self, task_id: str, request_id: str, approved: bool) -> bool:
        run = self._runs.get(task_id)
        return run is not None and run.interaction.decide(request_id, approved)

    async def stop(self, task_id: str, user_id: str | None = None) -> Task:
        if user_id is not None:
            await self._store.get(task_id, user_id)  # ownership
        run = self._runs.get(task_id)
        if run is not None:
            await self._cancel(run, "cancelled")
            return await self._store.get(task_id)

        task = await self._store.get(task_id)
        if task.status == "queued":
            await self._store.update(task_id, status="cancelled")
            await self._store.append_event(
                Event(type="task_status", task_id=task_id, status="cancelled")
            )
            task = await self._store.get(task_id)
        return task

    async def recover(self) -> None:
        """At startup: tasks left in progress by a previous run of the server
        can't continue, so mark them interrupted; then apply retention."""
        count = await self._store.interrupt_unfinished()
        if count:
            log.info("tasks_interrupted_at_startup", count=count)
        await self.purge_expired()

    async def purge_expired(self) -> int:
        days = self._settings.task_retention_days
        if days <= 0:
            return 0
        removed = await self._store.purge_before(utcnow() - timedelta(days=days))
        if removed:
            log.info("tasks_purged", count=removed, retention_days=days)
        return removed

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            await self._cancel(run, "interrupted")
        for task_id in list(self._sessions):
            await self._end_session(task_id, "shutdown")

    # ------------------------------------------------------------------- runs

    async def _start(self, task: Task, user_id: str, send: Subscriber) -> None:
        publisher = EventPublisher(task.id, self._store)
        publisher.set_subscriber(send)

        async def send_request(
            call_id: str, tool: str, args: dict[str, Any], confirmed: bool
        ) -> None:
            publisher.publish(
                "tool_start",
                call_id=call_id,
                tool=tool,
                input=args,
                message=describe_call(tool, args),
                confirmed=True if confirmed else None,
            )

        async def on_waiting(waiting: bool) -> None:
            status: TaskStatus = "waiting" if waiting else "running"
            await self._store.update(task.id, status=status)
            publisher.publish("task_status", status=status)

        bridge = BrowserBridge(send_request, self._settings.tool_timeout_seconds)
        interaction = UserInteraction(
            publisher.publish,
            on_waiting,
            self._settings.user_reply_timeout_seconds,
            self._settings.confirmation_timeout_seconds,
        )
        run = _Run(
            task_id=task.id,
            user_id=user_id,
            publisher=publisher,
            bridge=bridge,
            interaction=interaction,
        )
        # Mark it running before the lock is released so a second attach
        # can never start it twice.
        await self._store.update(task.id, status="running")
        self._runs[task.id] = run
        publisher.start()
        run.job = asyncio.create_task(self._execute(run, task), name=f"task:{task.id}")

    async def _cancel(self, run: _Run, reason: TaskStatus) -> None:
        job = run.job
        if job is None or job.done():
            return
        if run.finishing:
            await asyncio.wait({job})
            return
        run.cancel_reason = reason
        run.bridge.close("The task was stopped.")
        run.interaction.close()
        job.cancel()
        await asyncio.wait({job})

    async def _memory_for(self, user_id: str) -> list[str]:
        if self._memory is None:
            return []
        try:
            return [item.content for item in await self._memory.list(user_id)]
        except Exception:
            log.exception("memory_load_failed", user_id=user_id)
            return []

    async def _execute(self, run: _Run, task: Task) -> None:
        status: TaskStatus = "failed"
        result: str | None = None
        error: str | None = None
        outcome: TaskOutcome | None = None
        saved = 0
        messages: list[BaseMessage] = []
        try:
            run.publisher.publish("task_status", status="running")
            page = task.page
            context = AgentContext(
                task_id=task.id,
                provider=self._provider,
                registry=self._registry,
                bridge=run.bridge,
                publish=run.publisher.publish,
                recorder=_StoreRecorder(self._store, task.id),
                interaction=run.interaction,
                limits=ToolLimits(
                    page_text=self._settings.agent_page_text_limit,
                    elements=self._settings.agent_element_limit,
                    max_steps=self._settings.agent_max_steps,
                ),
                tabs=TabState(page.tab_id, page.url, page.title) if page else TabState(),
                domains=self._domains,
                confirmations=self._confirmations,
            )
            memory = await self._memory_for(run.user_id)
            first = HumanMessage(task_context(task.goal, task.page, utcnow(), memory))
            state: AgentState = {"messages": [first]}
            # Stream the graph's state so the conversation is stored as it
            # grows, not only when the run ends.
            async for value in self._graph.astream(
                {"messages": [first]},
                context=context,
                config={"recursion_limit": recursion_limit(self._settings.agent_max_steps)},
                stream_mode="values",
            ):
                state = cast(AgentState, value)
                messages = list(state.get("messages", []))
                saved = await self._save_messages(task.id, messages, saved)
            status, result, error, outcome = _outcome(state, self._settings.agent_max_steps)
        except asyncio.CancelledError:
            status = run.cancel_reason or "cancelled"
            error = (
                "Stopped at your request."
                if status == "cancelled"
                else "The browser disconnected before the task finished."
            )
        except GraphRecursionError:
            status = "failed"
            error = _step_limit_message(self._settings.agent_max_steps)
        except Exception as exc:
            log.exception("task_failed", task_id=task.id)
            status = "failed"
            error = _friendly_error(exc)
        finally:
            run.finishing = True
            run.bridge.close("The task has ended.")
            run.interaction.close()

        try:
            await self._save_messages(task.id, messages, saved)
            await self._store.update(
                task.id, status=status, result=result, error=error, outcome=outcome
            )
            if error and status == "failed":
                run.publisher.publish("error", message=error)
            run.publisher.publish("task_status", status=status, message=error)
            await run.publisher.close()
        finally:
            self._runs.pop(task.id, None)
            self._store.forget(task.id)
            log.info("task_finished", task_id=task.id, status=status)

    async def _save_messages(self, task_id: str, messages: list[BaseMessage], saved: int) -> int:
        if len(messages) <= saved:
            return saved
        try:
            await self._store.save_messages(task_id, saved, messages[saved:])
        except Exception:
            log.exception("messages_save_failed", task_id=task_id)
            return saved
        return len(messages)


def _session_key(task_id: str) -> str:
    return f"task-session:{task_id}"


class _StoreRecorder:
    def __init__(self, store: TaskStore, task_id: str) -> None:
        self._store = store
        self._task_id = task_id

    async def step_started(
        self, call_id: str, tool: str, args: dict[str, Any], tab_id: int | None
    ) -> None:
        await self._store.add_step(
            self._task_id, TaskStep(call_id=call_id, tool=tool, input=args, tab_id=tab_id)
        )

    async def step_finished(self, call_id: str, result: ToolResult, message: str) -> None:
        await self._store.finish_step(
            self._task_id, call_id, success=result.success, message=message, error=result.error
        )

    async def tabs_changed(self, tabs: TabState) -> None:
        await self._store.update(
            self._task_id, current_tab=tabs.current, current_url=tabs.url, tabs=tabs.snapshot()
        )


Outcome = tuple[TaskStatus, str | None, str | None, TaskOutcome | None]


def _outcome(state: AgentState, max_steps: int) -> Outcome:
    finish = state.get("finish")
    if finish:
        return "completed", finish["answer"], None, finish["outcome"]
    if state.get("step_limit_reached"):
        return "failed", None, _step_limit_message(max_steps), None

    last = state["messages"][-1]
    text = message_text(last).strip() if isinstance(last, AIMessage) else ""
    stop = state.get("stop_reason", StopReason.END_TURN)
    match stop:
        case StopReason.REFUSAL:
            return "failed", None, "I can't help with that request.", None
        case StopReason.MAX_TOKENS:
            return "completed", (text + "\n\n(My answer was cut short.)").strip(), None, None
        case StopReason.END_TURN:
            return "completed", text or "Done.", None, None
        case _:
            if text:
                return "completed", text, None, None
            reason = state.get("raw_stop_reason") or "unknown"
            return "failed", None, f"The AI model stopped unexpectedly ({reason}).", None


def _step_limit_message(max_steps: int) -> str:
    return (
        f"I stopped after {max_steps} steps without finishing. Try breaking the request "
        "into smaller parts."
    )


def _friendly_error(exc: Exception) -> str:
    if is_quota_exhausted(exc):
        return (
            "The AI service says this API key has used up its quota (for example, the free "
            "tier's daily request limit). Try again later, switch to another model with "
            "LLM_MODEL, or enable billing for the key."
        )
    status = error_status(exc)
    if status in (401, 403):
        return "The AI service rejected the server's credentials. Check LLM_API_KEY on the backend."
    if status == 429:
        return "The AI service is busy right now. Please try again in a minute."
    if status == 400:
        return "The AI service rejected the request. Check the backend logs for details."
    if status is not None and status >= 500:
        return "The AI service is busy or unavailable right now. Please try again."
    if is_transient(exc):
        return "Couldn't reach the AI service. Check the backend's network connection."
    return "Something went wrong while working on this task."


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATUSES


__all__ = [
    "SessionAlreadyAttachedError",
    "TaskNotFoundError",
    "TaskService",
    "TaskStillRunningError",
    "TooManyTasksError",
]
