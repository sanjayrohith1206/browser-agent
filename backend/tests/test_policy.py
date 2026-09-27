"""Phase 4-5 agent behavior: domain restrictions, confirmations, tab state,
memory and stored history."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select

from app.agent.events import Event
from app.agent.policy import DomainPolicy
from app.agent.tabs import TabState
from app.config import Settings
from app.db import schema as t
from app.kv import MemoryKV
from app.tasks.models import TabInfo
from app.tasks.service import SessionAlreadyAttachedError
from app.tasks.store import TaskNotFoundError
from app.tools.registry import ToolRegistry
from app.tools.types import ToolError, ToolResult
from tests.conftest import DbFixture
from tests.fakes import PAGE, FakeBrowser, ScriptedChatModel, text_turn, tool_turn
from tests.test_agent_service import REQUEST, make_service, owner, run_task, tool_messages

CLICK = ("click_element", {"element_id": "el_a_1"})


def test_domain_policy() -> None:
    policy = DomainPolicy(blocked=["bank.example", "https://evil.example/path"])
    assert policy.refusal("https://www.bank.example/login")
    assert policy.refusal("https://evil.example/")
    assert policy.refusal("https://notbank.example/") is None
    assert policy.refusal("chrome://settings") is None  # left to the extension

    allow = DomainPolicy(allowed=["*.wikipedia.org"])
    assert allow.refusal("https://en.wikipedia.org/wiki/X") is None
    assert allow.refusal("https://google.com/")
    assert not DomainPolicy().restricted and allow.restricted


def test_tab_state() -> None:
    tabs = TabState(1, "https://a.example/", "A")
    assert tabs.observe(
        "open_tab", ToolResult.ok("open_tab", {"tab_id": 2, "url": "https://b.example/"})
    )
    assert (tabs.current, tabs.url) == (2, "https://b.example/")
    assert tabs.tabs[2].opened_by_agent and not tabs.tabs[1].opened_by_agent
    # An action that navigated within the tab.
    tabs.observe(
        "click_element",
        ToolResult.ok("click_element", {"navigated": True, "url": "https://b.example/2"}),
    )
    assert tabs.tabs[2].url == "https://b.example/2"
    tabs.observe(
        "close_tab",
        ToolResult.ok(
            "close_tab",
            {"closed_tab_id": 2, "current_tab": {"tab_id": 1, "url": "https://a.example/"}},
        ),
    )
    assert tabs.current == 1 and list(tabs.tabs) == [1]
    assert not tabs.observe("get_elements", ToolResult.fail("get_elements", "TIMEOUT", "slow"))


async def test_blocked_domains_never_reach_the_browser(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    settings = settings.model_copy(update={"blocked_domains": ["example.com"]})
    model = ScriptedChatModel(
        script=[
            tool_turn(("navigate", {"url": "https://shop.example.com/"})),
            # The task started on example.com, so reading it is refused too...
            tool_turn(("get_page_content", {})),
            # ...but moving away is allowed.
            tool_turn(("navigate", {"url": "https://other.test/"})),
            text_turn("Done."),
        ]
    )
    service = make_service(settings, registry, model, db)
    browser = FakeBrowser(
        handler=lambda tool, args: ToolResult.ok(tool, {"url": args["url"], "tab_id": 7})
    )
    task_id, browser = await run_task(service, browser)

    assert tool_messages(model, 1)[0]["error"]["code"] == "DOMAIN_BLOCKED"
    assert tool_messages(model, 2)[-1]["error"]["code"] == "DOMAIN_BLOCKED"
    started = [e for e in browser.events if e.type == "tool_start"]
    assert [(e.tool, (e.input or {}).get("url")) for e in started] == [
        ("navigate", "https://other.test/")
    ]
    assert (await service.get(task_id, owner(service))).current_url == "https://other.test/"


async def test_risk_levels_need_approval_before_running(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    settings = settings.model_copy(update={"confirm_risk_levels": ["interact"]})
    model = ScriptedChatModel(script=[tool_turn(CLICK), tool_turn(CLICK), text_turn("ok")])
    service = make_service(settings, registry, model, db)
    decisions = iter([True, False])
    browser = FakeBrowser(
        handler=lambda tool, args: ToolResult.ok(tool, {"clicked": 'button "Go"'}),
        approve=lambda event: next(decisions),
    )
    _, browser = await run_task(service, browser)

    requests = [e for e in browser.events if e.type == "confirmation_request"]
    assert [r.message for r in requests] == ["Clicking", "Clicking"]
    results = [e.approved for e in browser.events if e.type == "confirmation_result"]
    assert results == [True, False]
    started = [e for e in browser.events if e.type == "tool_start"]
    assert len(started) == 1 and started[0].confirmed is True
    assert tool_messages(model, 2)[-1]["error"]["code"] == "USER_DECLINED"
    statuses = [e.status for e in browser.events if e.type == "task_status"]
    assert statuses.count("waiting") == 2


def _guarded_click(tool: str, args: dict[str, Any]) -> ToolResult:
    """Plays an extension that flags the click as a purchase."""
    return ToolResult(
        success=False,
        tool=tool,
        error=ToolError(
            code="CONFIRMATION_REQUIRED",
            message="This would make a purchase or payment.",
            details={
                "action": "Click the “Place your order” button",
                "reason": "make a purchase or payment",
            },
        ),
    )


async def test_high_impact_clicks_run_once_approved(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(script=[tool_turn(CLICK), text_turn("Ordered.")])
    service = make_service(settings, registry, model, db)
    seen: list[Event] = []

    browser = FakeBrowser(approve=lambda event: True)

    async def record(event: Event) -> None:
        seen.append(event)
        if event.type == "tool_start":
            result = (
                ToolResult.ok("click_element", {"clicked": 'button "Place your order"'})
                if event.confirmed
                else _guarded_click("click_element", {})
            )
            assert event.call_id and browser.submit
            browser.submit(event.call_id, result)
        await browser(event)

    browser.handler = lambda tool, args: None  # answered by `record`
    task = await service.create(owner(service), REQUEST)
    browser.submit = lambda call_id, result: service.submit_tool_result(task.id, call_id, result)
    browser.decide = lambda rid, ok: service.submit_decision(task.id, rid, ok)
    await service.attach(task.id, owner(service), record)
    await asyncio.wait_for(browser.done.wait(), timeout=5)

    request = next(e for e in seen if e.type == "confirmation_request")
    assert request.message == "Click the “Place your order” button"
    assert request.reason == "This would make a purchase or payment."
    starts = [e.confirmed for e in seen if e.type == "tool_start"]
    assert starts == [None, True]
    assert tool_messages(model, 1)[0]["success"] is True
    stored = await service.get(task.id, owner(service))
    assert [s.success for s in stored.steps] == [False, True]


async def test_declined_high_impact_click(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(script=[tool_turn(CLICK), text_turn("Okay, I stopped.")])
    service = make_service(settings, registry, model, db)
    browser = FakeBrowser(handler=_guarded_click, approve=lambda e: False)
    _, browser = await run_task(service, browser)
    assert [e.type for e in browser.events].count("tool_start") == 1
    result = tool_messages(model, 1)[0]
    assert result["error"]["code"] == "USER_DECLINED" and "Don't retry" in result["hint"]


async def test_tabs_are_tracked_on_the_task(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    def handler(tool: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult.ok(tool, {"tab_id": 12, "url": args["url"], "title": "Laptops B"})

    model = ScriptedChatModel(
        script=[tool_turn(("open_tab", {"url": "https://b.example/laptops"})), text_turn("ok")]
    )
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service, FakeBrowser(handler=handler))
    task = await service.get(task_id, owner(service))
    assert task.current_tab == 12 and task.current_url == "https://b.example/laptops"
    assert task.tabs == [
        TabInfo(tab_id=7, url=PAGE["url"], title=PAGE["title"]),
        TabInfo(
            tab_id=12, url="https://b.example/laptops", title="Laptops B", opened_by_agent=True
        ),
    ]
    assert task.steps[0].tab_id == 7  # the tab it was issued in


async def test_memory_is_given_to_the_agent_and_history_is_stored(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    await db.database.memory.add(db.user_id, "I prefer vegetarian restaurants")
    model = ScriptedChatModel(
        script=[tool_turn(("get_page_content", {})), text_turn("Here are three.")]
    )
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service)

    first = str(model.requests[0][1].content)
    assert "- I prefer vegetarian restaurants" in first
    stored = await db.database.tasks.messages(task_id)
    assert [m.type for m in stored] == ["human", "ai", "tool", "ai"]
    async with db.database.engine.connect() as conn:
        sessions = (await conn.execute(select(t.browser_sessions))).all()
    assert len(sessions) == 1 and sessions[0].task_id == task_id


async def test_one_browser_session_per_task(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    kv = MemoryKV()
    model = ScriptedChatModel(script=[text_turn("Hi!")])
    service = make_service(settings, registry, model, db, kv=kv)
    task = await service.create(owner(service), REQUEST)
    other = await db.database.users.create("other@example.com", "x")
    with pytest.raises(TaskNotFoundError):
        await service.attach(task.id, other.id, FakeBrowser())
    # The lock is shared: a second instance can't attach either.
    await kv.acquire(f"task-session:{task.id}", "another-instance", 60)
    with pytest.raises(SessionAlreadyAttachedError):
        await service.attach(task.id, owner(service), FakeBrowser())
