"""The agent loop end to end through TaskService, with a scripted model and a
simulated browser."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from app.config import Settings
from app.kv import MemoryKV
from app.llm.provider import LangChainProvider
from app.tasks.models import CreateTaskRequest, PageContext
from app.tasks.service import TaskService
from app.tasks.store import TRANSIENT_EVENTS
from app.tools.registry import ToolRegistry
from app.tools.types import ToolResult
from tests.conftest import DbFixture
from tests.fakes import (
    PAGE,
    FakeBrowser,
    ScriptedChatModel,
    default_browser,
    raw_tool_turn,
    text_turn,
    tool_turn,
)

REQUEST = CreateTaskRequest(
    prompt="Read this page and summarize it.",
    page=PageContext(tab_id=7, url=PAGE["url"], title=PAGE["title"]),
)


class OwnedService(TaskService):
    """A TaskService plus the user whose tasks the test runs."""

    user_id: str


def make_service(
    settings: Settings,
    registry: ToolRegistry,
    model: ScriptedChatModel,
    db: DbFixture,
    kv: MemoryKV | None = None,
) -> OwnedService:
    service = OwnedService(
        db.database.tasks,
        LangChainProvider("test", model),
        registry,
        settings,
        kv=kv or MemoryKV(),
        memory=db.database.memory,
        session_log=db.database.browser_sessions,
    )
    service.user_id = db.user_id
    return service


def owner(service: OwnedService) -> str:
    return service.user_id


async def run_task(
    service: OwnedService, browser: FakeBrowser | None = None, request: CreateTaskRequest = REQUEST
) -> tuple[str, FakeBrowser]:
    task = await service.create(owner(service), request)
    browser = browser or FakeBrowser()
    browser.submit = lambda call_id, result: service.submit_tool_result(task.id, call_id, result)
    browser.reply = lambda request_id, text: service.submit_answer(task.id, request_id, text)
    browser.decide = lambda request_id, ok: service.submit_decision(task.id, request_id, ok)
    await service.attach(task.id, owner(service), browser)
    await asyncio.wait_for(browser.done.wait(), timeout=5)
    return task.id, browser


def tool_messages(model: ScriptedChatModel, request_index: int) -> list[dict[str, Any]]:
    return [
        json.loads(str(m.content))
        for m in model.requests[request_index]
        if isinstance(m, ToolMessage)
    ]


async def test_summarize_page(settings: Settings, registry: ToolRegistry, db: DbFixture) -> None:
    model = ScriptedChatModel(
        script=[
            tool_turn(("get_page_content", {}), text="Let me read the page."),
            text_turn("**Summary:** The page is about browsers.", reasoning="Summarize it."),
        ]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service, FakeBrowser())

    task = await service.get(task_id, owner(service))
    assert task.status == "completed"
    assert task.result == "**Summary:** The page is about browsers."
    assert task.current_url == PAGE["url"]
    assert [(s.tool, s.success, s.message) for s in task.steps] == [
        ("get_page_content", True, "Read “Example Article” (33 characters)")
    ]

    # Streamed events, in order, with contiguous sequence numbers.
    types = browser.types()
    assert types[0:2] == ["task_status", "task_status"]
    assert types.index("tool_start") < types.index("tool_result")
    assert types.count("agent_message") == 2
    assert "agent_thinking" in types
    assert [e.seq for e in browser.events] == list(range(1, len(browser.events) + 1))
    assert browser.events[-1].status == "completed"
    tool_start = next(e for e in browser.events if e.type == "tool_start")
    assert tool_start.message == "Reading the page"
    assert tool_start.input == {"max_chars": 40000}  # the page-text budget is always applied

    # The first request carries the page context; the second carries the
    # structured tool result.
    first_user = str(model.requests[0][1].content)
    assert PAGE["url"] in first_user and "Read this page and summarize it." in first_user
    assert tool_messages(model, 1) == [
        {"success": True, "tool": "get_page_content", "result": PAGE}
    ]


async def test_tool_failure_is_reported_to_model(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    def browser_handler(tool: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult.fail(tool, "PAGE_NOT_SCRIPTABLE", "chrome:// pages cannot be read")

    model = ScriptedChatModel(
        script=[
            tool_turn(("get_page_content", {})),
            text_turn("I can't read this page because Chrome blocks extensions on it."),
        ]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service, FakeBrowser(handler=browser_handler))

    task = await service.get(task_id, owner(service))
    assert task.status == "completed"
    assert task.steps[0].success is False
    assert task.steps[0].error is not None and task.steps[0].error.code == "PAGE_NOT_SCRIPTABLE"
    result_event = next(e for e in browser.events if e.type == "tool_result")
    assert result_event.success is False
    assert result_event.message == "This page can't be used by extensions"
    assert tool_messages(model, 1)[0]["error"]["code"] == "PAGE_NOT_SCRIPTABLE"


async def test_invalid_arguments_never_reach_browser(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(
        script=[
            tool_turn(("get_page_content", {"max_chars": 1})),
            raw_tool_turn("toolu_bad", "get_page_content", "{not json"),
            tool_turn(("delete_everything", {})),
            text_turn("Done."),
        ]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service)

    assert "tool_start" not in browser.types()
    assert (await service.get(task_id, owner(service))).status == "completed"
    assert tool_messages(model, 1)[0]["error"]["code"] == "INVALID_INPUT"
    assert tool_messages(model, 2)[-1]["error"]["code"] == "INVALID_INPUT"
    assert tool_messages(model, 3)[-1]["error"]["code"] == "UNKNOWN_TOOL"


async def test_step_limit(settings: Settings, registry: ToolRegistry, db: DbFixture) -> None:
    model = ScriptedChatModel(
        script=[tool_turn(("get_current_page", {})) for _ in range(settings.agent_max_steps + 1)]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service)

    task = await service.get(task_id, owner(service))
    assert task.status == "failed"
    assert task.error is not None and "steps" in task.error
    assert "error" in browser.types()


async def test_stop_cancels_running_task(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(script=[tool_turn(("get_page_content", {}))])
    service = make_service(settings, registry, model, db)
    task = await service.create(owner(service), REQUEST)
    # This browser never answers, so the run waits on the tool call.
    browser = FakeBrowser(handler=lambda tool, args: None)
    browser.submit = lambda call_id, result: None
    await service.attach(task.id, owner(service), browser)
    await asyncio.wait_for(browser.tool_started.wait(), timeout=5)

    stopped = await service.stop(task.id)
    assert stopped.status == "cancelled"
    assert stopped.error == "Stopped at your request."
    assert browser.events[-1].status == "cancelled"


async def test_disconnect_interrupts_task(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(script=[tool_turn(("get_page_content", {}))])
    service = make_service(settings, registry, model, db)
    task = await service.create(owner(service), REQUEST)
    browser = FakeBrowser(handler=lambda tool, args: None)
    await service.attach(task.id, owner(service), browser)
    await asyncio.wait_for(browser.tool_started.wait(), timeout=5)

    await service.detach(task.id)
    assert (await service.get(task.id, owner(service))).status == "interrupted"


async def test_reattach_replays_history(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(script=[text_turn("Hi!")])
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service)
    await service.detach(task_id)

    replay = FakeBrowser()
    await service.attach(task_id, owner(service), replay, after_seq=0)
    # Streaming fragments are not stored; the complete messages are.
    durable = [e for e in browser.events if e.type not in TRANSIENT_EVENTS]
    assert [e.seq for e in replay.events] == [e.seq for e in durable]
    assert [e.type for e in replay.events] == [e.type for e in durable]
    assert "agent_message" in [e.type for e in replay.events]
    assert len(model.requests) == 1  # finished tasks do not run again


async def test_provider_error_fails_with_friendly_message(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    class AuthError(Exception):
        status_code = 401

    model = ScriptedChatModel(script=[], error=AuthError("invalid x-api-key"))
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service)

    task = await service.get(task_id, owner(service))
    assert task.status == "failed"
    assert task.error is not None and "LLM_API_KEY" in task.error
    assert "x-api-key" not in task.error


async def test_refusal(settings: Settings, registry: ToolRegistry, db: DbFixture) -> None:
    model = ScriptedChatModel(script=[text_turn("", stop="refusal")])
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service)
    task = await service.get(task_id, owner(service))
    assert task.status == "failed"
    assert task.error == "I can't help with that request."


@pytest.mark.parametrize("tool", ["get_current_page", "get_page_content"])
async def test_default_browser_results_are_valid(tool: str) -> None:
    assert default_browser(tool, {}).success


async def test_screenshot_is_sent_as_an_image(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    def browser_handler(tool: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult.ok(
            tool,
            {"image_data_url": "data:image/jpeg;base64,QUJD", "url": PAGE["url"], "title": "T"},
        )

    model = ScriptedChatModel(
        script=[tool_turn(("take_screenshot", {})), text_turn("The page shows a chart.")]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service, FakeBrowser(handler=browser_handler))

    assert (await service.get(task_id, owner(service))).status == "completed"
    second = model.requests[1]
    tool_msg = next(m for m in second if isinstance(m, ToolMessage))
    assert "QUJD" not in str(tool_msg.content)
    assert json.loads(str(tool_msg.content))["result"]["image"].startswith("The screenshot")
    image_msg = second[-1]
    assert isinstance(image_msg, HumanMessage)
    blocks = image_msg.content
    assert isinstance(blocks, list) and blocks[1] == {
        "type": "image",
        "base64": "QUJD",
        "mime_type": "image/jpeg",
    }
    # The browser event stream never carries the image.
    assert all("QUJD" not in e.model_dump_json() for e in browser.events)


async def test_current_url_follows_navigation(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    def browser_handler(tool: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult.ok(tool, {"url": args["url"], "title": "Wiki", "loaded": True})

    model = ScriptedChatModel(
        script=[tool_turn(("navigate", {"url": "https://en.wikipedia.org/"})), text_turn("Opened.")]
    )
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service, FakeBrowser(handler=browser_handler))
    assert (await service.get(task_id, owner(service))).current_url == "https://en.wikipedia.org/"


async def test_quota_exhaustion_is_explained(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    class QuotaError(Exception):
        code = 429

    model = ScriptedChatModel(
        script=[],
        error=QuotaError("You exceeded your current quota ... GenerateRequestsPerDayPerProject"),
    )
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service)
    task = await service.get(task_id, owner(service))
    assert task.status == "failed"
    assert task.error is not None and "quota" in task.error
    assert len(model.requests) == 1  # not retried


async def test_tool_output_is_kept_within_budget(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    settings = settings.model_copy(
        update={"agent_page_text_limit": 8000, "agent_element_limit": 60}
    )
    seen: list[tuple[str, dict[str, Any]]] = []

    def browser_handler(tool: str, args: dict[str, Any]) -> ToolResult:
        seen.append((tool, args))
        return (
            default_browser(tool, args) if tool == "get_page_content" else ToolResult.ok(tool, {})
        )

    model = ScriptedChatModel(
        script=[
            tool_turn(("get_page_content", {"max_chars": 100000})),
            tool_turn(("get_page_content", {"max_chars": 2000})),
            tool_turn(("get_elements", {})),
            text_turn("Done."),
        ]
    )
    service = make_service(settings, registry, model, db)
    await run_task(service, FakeBrowser(handler=browser_handler))
    assert seen == [
        ("get_page_content", {"max_chars": 8000}),  # clamped
        ("get_page_content", {"max_chars": 2000}),  # smaller requests kept
        ("get_elements", {"max_elements": 60}),  # default filled in
    ]


# ------------------------------------------------------------------ phase 3


async def test_finish_task_ends_with_streamed_answer(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    answer = "**Summary:** The page is about browsers."
    model = ScriptedChatModel(
        script=[
            tool_turn(("get_page_content", {})),
            tool_turn(("finish_task", {"answer": answer, "outcome": "done"})),
        ]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service)

    task = await service.get(task_id, owner(service))
    assert task.status == "completed"
    assert task.result == answer
    assert task.outcome == "done"
    # finish_task runs in the agent loop, never in the browser.
    assert [e.tool for e in browser.events if e.type == "tool_start"] == ["get_page_content"]
    final = [e for e in browser.events if e.type == "agent_message"]
    assert final[-1].message == answer and final[-1].message_id == "final_toolu_0_finish_task"
    streamed = "".join(
        e.delta or ""
        for e in browser.events
        if e.type == "agent_message_delta" and e.message_id == final[-1].message_id
    )
    assert streamed == answer
    assert len(model.requests) == 2  # no model call after finishing


async def test_nothing_runs_after_finish_task(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(
        script=[
            tool_turn(
                ("finish_task", {"answer": "Blocked by a login page.", "outcome": "blocked"}),
                ("get_page_content", {}),
            )
        ]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service)
    task = await service.get(task_id, owner(service))
    assert (task.status, task.outcome) == ("completed", "blocked")
    assert "tool_start" not in browser.types()


async def test_invalid_finish_is_returned_to_model(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(
        script=[
            tool_turn(("finish_task", {"answer": "Done."})),
            tool_turn(("finish_task", {"answer": "Done.", "outcome": "done"})),
        ]
    )
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service)
    assert (await service.get(task_id, owner(service))).status == "completed"
    assert tool_messages(model, 1)[0]["error"]["code"] == "INVALID_INPUT"


async def test_ask_user_waits_for_the_answer(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(
        script=[
            tool_turn(("ask_user", {"question": "Which size?", "options": ["Small", "Large"]})),
            text_turn("Large it is."),
        ]
    )
    service = make_service(settings, registry, model, db)
    browser = FakeBrowser(answer=lambda event: "Large")
    task_id, browser = await run_task(service, browser)

    assert (await service.get(task_id, owner(service))).status == "completed"
    question = next(e for e in browser.events if e.type == "user_question")
    assert question.question == "Which size?" and question.options == ["Small", "Large"]
    reply = next(e for e in browser.events if e.type == "user_reply")
    assert reply.request_id == question.request_id and reply.message == "Large"
    statuses = [e.status for e in browser.events if e.type == "task_status"]
    assert statuses == ["queued", "running", "waiting", "running", "completed"]
    assert tool_messages(model, 1) == [
        {"success": True, "tool": "ask_user", "result": {"answer": "Large"}}
    ]
    assert "tool_start" not in browser.types()


async def test_unanswered_question_times_out(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    settings = settings.model_copy(update={"user_reply_timeout_seconds": 0.05})
    model = ScriptedChatModel(
        script=[tool_turn(("ask_user", {"question": "Which size?"})), text_turn("I'll stop.")]
    )
    service = make_service(settings, registry, model, db)
    task_id, browser = await run_task(service, FakeBrowser(answer=lambda event: None))
    assert (await service.get(task_id, owner(service))).status == "completed"
    assert tool_messages(model, 1)[0]["error"]["code"] == "TIMEOUT"
    assert "user_reply" not in browser.types()


async def test_stop_while_waiting_for_the_user(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    model = ScriptedChatModel(script=[tool_turn(("ask_user", {"question": "Which size?"}))])
    service = make_service(settings, registry, model, db)
    task = await service.create(owner(service), REQUEST)
    browser = FakeBrowser()
    await service.attach(task.id, owner(service), browser)
    for _ in range(100):
        if "user_question" in browser.types():
            break
        await asyncio.sleep(0.01)
    assert (await service.get(task.id, owner(service))).status == "waiting"

    stopped = await service.stop(task.id)
    assert stopped.status == "cancelled"
    assert not service.submit_answer(task.id, "ask_whatever", "Large")


async def test_step_budget_warns_then_stops(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    # max_steps is 5 in tests.
    model = ScriptedChatModel(
        script=[tool_turn(("get_current_page", {})) for _ in range(5)]
        + [tool_turn(("finish_task", {"answer": "Partly done.", "outcome": "partial"}))]
    )
    service = make_service(settings, registry, model, db)
    task_id, _ = await run_task(service)

    notes = [tool_messages(model, i)[-1].get("system_note") for i in range(1, 6)]
    assert notes[:2] == [None, None]
    assert notes[2] is not None and "Only 2 steps left" in notes[2]
    assert notes[3] is not None and "Only 1 step left" in notes[3]
    assert notes[4] is not None and "no steps left" in notes[4]
    task = await service.get(task_id, owner(service))
    assert (task.status, task.outcome, task.result) == ("completed", "partial", "Partly done.")


async def test_repeated_failures_get_recovery_hints(
    settings: Settings, registry: ToolRegistry, db: DbFixture
) -> None:
    def browser_handler(tool: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult.fail(tool, "ELEMENT_NOT_FOUND", "Element el_x_1 is gone.")

    click = ("click_element", {"element_id": "el_x_1"})
    model = ScriptedChatModel(script=[tool_turn(click), tool_turn(click), text_turn("Stuck.")])
    service = make_service(settings, registry, model, db)
    await run_task(service, FakeBrowser(handler=browser_handler))

    first = tool_messages(model, 1)[0]["hint"]
    second = tool_messages(model, 2)[-1]["hint"]
    assert "fresh IDs" in first and "failed 2 times" not in first
    assert "failed 2 times" in second
