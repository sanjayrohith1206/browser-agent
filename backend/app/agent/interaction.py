"""Pausing the agent for the user.

The agent loop asks the user a question, or for approval of a high-impact
action, through UserInteraction. The request travels to the side panel as
an event, and the answer comes back over the task's WebSocket. While it
waits the task is marked "waiting".
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

MAX_ANSWER_CHARS = 4000


class Publish(Protocol):
    def __call__(self, type: str, **fields: Any) -> None: ...


# Called with True when the agent starts waiting for the user, False after.
WaitingHook = Callable[[bool], Awaitable[None]]


class UserInteraction:
    def __init__(
        self,
        publish: Publish,
        on_waiting: WaitingHook,
        timeout_seconds: float,
        confirmation_timeout_seconds: float = 300.0,
    ) -> None:
        self._publish = publish
        self._on_waiting = on_waiting
        self._timeout = timeout_seconds
        self._confirmation_timeout = confirmation_timeout_seconds
        self._pending: dict[str, asyncio.Future[str | None]] = {}
        self._confirmations: dict[str, asyncio.Future[bool]] = {}
        self._closed = False

    async def ask(self, question: str, options: list[str] | None = None) -> str | None:
        """Ask the user and wait. Returns None if they did not answer in time
        or the session ended."""
        if self._closed:
            return None
        request_id = f"ask_{uuid.uuid4().hex[:16]}"
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._publish(
            "user_question", request_id=request_id, question=question, options=options or None
        )
        await self._on_waiting(True)
        try:
            answer = await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            answer = None
        finally:
            self._pending.pop(request_id, None)
        # Not reached on cancellation: the task's final status replaces "waiting".
        await self._on_waiting(False)
        if answer is not None:
            self._publish("user_reply", request_id=request_id, message=answer)
        return answer

    async def confirm(self, action: str, reason: str) -> bool:
        """Ask the user to approve an action. Anything but an explicit
        approval (declining, no answer in time, the panel closing) is a no."""
        if self._closed:
            return False
        request_id = f"confirm_{uuid.uuid4().hex[:16]}"
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._confirmations[request_id] = future
        self._publish("confirmation_request", request_id=request_id, message=action, reason=reason)
        await self._on_waiting(True)
        try:
            approved = await asyncio.wait_for(future, timeout=self._confirmation_timeout)
        except TimeoutError:
            approved = False
        finally:
            self._confirmations.pop(request_id, None)
        await self._on_waiting(False)
        self._publish("confirmation_result", request_id=request_id, approved=approved)
        return approved

    def decide(self, request_id: str, approved: bool) -> bool:
        """Deliver the user's decision. False for unknown or settled requests."""
        future = self._confirmations.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def answer(self, request_id: str, text: str) -> bool:
        """Deliver the user's answer. False for unknown or settled requests."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(text.strip()[:MAX_ANSWER_CHARS])
        return True

    def close(self) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_result(None)
        for decision in self._confirmations.values():
            if not decision.done():
                decision.set_result(False)
