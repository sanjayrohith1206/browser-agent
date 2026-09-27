from __future__ import annotations

import asyncio
from typing import Any

from app.browser.bridge import BrowserBridge
from app.tools.types import ToolResult


async def test_call_resolves_with_extension_result() -> None:
    sent: list[tuple[str, str, dict[str, Any]]] = []
    bridge: BrowserBridge

    async def send(call_id: str, tool: str, args: dict[str, Any], confirmed: bool) -> None:
        sent.append((call_id, tool, args))
        asyncio.get_running_loop().call_soon(
            bridge.resolve, call_id, ToolResult.ok(tool, {"url": "https://a.test"})
        )

    bridge = BrowserBridge(send, timeout_seconds=1)
    result = await bridge.call("c1", "get_current_page", {})
    assert sent == [("c1", "get_current_page", {})]
    assert result.success and result.result == {"url": "https://a.test"}
    # Late duplicates are ignored.
    assert not bridge.resolve("c1", ToolResult.ok("get_current_page", {}))


async def test_call_times_out() -> None:
    async def send(call_id: str, tool: str, args: dict[str, Any], confirmed: bool) -> None:
        return None

    bridge = BrowserBridge(send, timeout_seconds=0.05)
    result = await bridge.call("c1", "get_page_content", {})
    assert not result.success
    assert result.error is not None and result.error.code == "TIMEOUT"


async def test_close_fails_pending_and_future_calls() -> None:
    async def send(call_id: str, tool: str, args: dict[str, Any], confirmed: bool) -> None:
        return None

    bridge = BrowserBridge(send, timeout_seconds=5)
    pending = asyncio.create_task(bridge.call("c1", "get_page_content", {}))
    await asyncio.sleep(0)
    bridge.close("browser went away")
    result = await pending
    assert result.error is not None and result.error.code == "BROWSER_DISCONNECTED"

    later = await bridge.call("c2", "get_page_content", {})
    assert later.error is not None and later.error.message == "browser went away"
