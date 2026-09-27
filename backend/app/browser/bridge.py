"""Remote execution of browser tools.

Browser tools run inside the Chrome extension. The agent asks the bridge to
run a tool; the bridge emits a tool_start event over the task's WebSocket and
waits for the extension to answer with a matching tool_result message.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.tools.types import ToolResult

# Sends a tool request to the extension (it is emitted as a tool_start
# event): call_id, tool, args, and whether the user approved the call.
RequestSender = Callable[[str, str, dict[str, Any], bool], Awaitable[None]]


class BrowserBridge:
    def __init__(self, send_request: RequestSender, timeout_seconds: float) -> None:
        self._send = send_request
        self._timeout = timeout_seconds
        self._pending: dict[str, asyncio.Future[ToolResult]] = {}
        self._closed_reason: str | None = None

    async def call(
        self, call_id: str, tool: str, args: dict[str, Any], *, confirmed: bool = False
    ) -> ToolResult:
        if self._closed_reason is not None:
            return ToolResult.fail(tool, "BROWSER_DISCONNECTED", self._closed_reason)

        future: asyncio.Future[ToolResult] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = future
        try:
            await self._send(call_id, tool, args, confirmed)
            return await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            return ToolResult.fail(
                tool,
                "TIMEOUT",
                f"The browser did not respond within {self._timeout:.0f} seconds.",
            )
        finally:
            self._pending.pop(call_id, None)

    def resolve(self, call_id: str, result: ToolResult) -> bool:
        """Deliver a result from the extension. Returns False for unknown or
        already-settled calls (late or duplicate answers are ignored)."""
        future = self._pending.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True

    def close(self, reason: str) -> None:
        """Fail all in-flight calls and reject new ones."""
        self._closed_reason = reason
        for call_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(ToolResult.fail("unknown", "BROWSER_DISCONNECTED", reason))
            self._pending.pop(call_id, None)
