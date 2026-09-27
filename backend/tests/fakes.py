"""Test doubles: a scripted LangChain chat model and a simulated browser."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from app.agent.events import Event
from app.tools.types import ToolResult


def text_turn(text: str, *, reasoning: str | None = None, stop: str = "end_turn") -> dict[str, Any]:
    return {"text": text, "reasoning": reasoning, "tool_calls": [], "stop": stop}


def tool_turn(*calls: tuple[str, dict[str, Any]], text: str = "") -> dict[str, Any]:
    return {
        "text": text,
        "reasoning": None,
        "tool_calls": [
            {"id": f"toolu_{i}_{name}", "name": name, "args": args}
            for i, (name, args) in enumerate(calls)
        ],
        "stop": "tool_use",
    }


def raw_tool_turn(call_id: str, name: str, raw_args: str) -> dict[str, Any]:
    """A tool call whose argument JSON is malformed."""
    return {
        "text": "",
        "reasoning": None,
        "tool_calls": [],
        "raw_tool_calls": [{"id": call_id, "name": name, "args": raw_args}],
        "stop": "tool_use",
    }


class ScriptedChatModel(BaseChatModel):
    """Streams pre-scripted turns. Records every request it receives."""

    script: list[dict[str, Any]]
    requests: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)
    error: Exception | None = None
    # Raised (and consumed) one per request before `error`/`script` apply.
    transient_errors: list[Exception] = Field(default_factory=list)
    # Raised (and consumed) one per request after the first text chunk.
    midstream_errors: list[Exception] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:  # type: ignore[override]
        self.bound_tools = list(tools)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        chunks = list(self._chunks(messages))
        merged = chunks[0].message
        for c in chunks[1:]:
            merged = merged + c.message
        return ChatResult(generations=[ChatGeneration(message=AIMessage(**merged.model_dump()))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from self._chunks(messages)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._chunks(messages):
            await asyncio.sleep(0)
            yield chunk

    def _chunks(self, messages: list[BaseMessage]) -> Iterator[ChatGenerationChunk]:
        self.requests.append(list(messages))
        if self.transient_errors:
            raise self.transient_errors.pop(0)
        if self.error is not None:
            raise self.error
        if not self.script:
            raise AssertionError("ScriptedChatModel ran out of scripted turns")
        turn = self.script.pop(0)
        if turn["reasoning"]:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=[{"type": "reasoning", "reasoning": turn["reasoning"]}]
                )
            )
        for i, word in enumerate(_split_keep_spaces(turn["text"])):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word))
            if i == 0 and self.midstream_errors:
                self.script.insert(0, turn)  # the retry replays this turn
                raise self.midstream_errors.pop(0)
        tool_chunks = [
            {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
            for i, c in enumerate(turn["tool_calls"])
        ]
        tool_chunks += [
            {"name": c["name"], "args": c["args"], "id": c["id"], "index": 100 + i}
            for i, c in enumerate(turn.get("raw_tool_calls", []))
        ]
        if tool_chunks:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_call_chunks=tool_chunks)
            )
        yield ChatGenerationChunk(
            message=AIMessageChunk(content="", response_metadata={"stop_reason": turn["stop"]})
        )


def _split_keep_spaces(text: str) -> list[str]:
    return re.findall(r"\S+\s*", text)


PAGE = {
    "url": "https://example.com/article",
    "title": "Example Article",
    "description": "An example",
    "lang": "en",
    "headings": [{"level": 1, "text": "Example Article"}],
    "text": "Example body text about browsers.",
    "total_chars": 33,
    "truncated": False,
}


def default_browser(tool: str, args: dict[str, Any]) -> ToolResult:
    if tool == "get_current_page":
        return ToolResult.ok(
            tool,
            {
                "tab_id": 7,
                "window_id": 1,
                "url": PAGE["url"],
                "title": PAGE["title"],
                "status": "complete",
            },
        )
    if tool == "get_page_content":
        return ToolResult.ok(tool, PAGE)
    return ToolResult.fail(tool, "UNKNOWN_TOOL", tool)


class FakeBrowser:
    """Plays the extension: collects events and answers tool_start requests."""

    def __init__(
        self,
        submit: Callable[[str, ToolResult], Any] | None = None,
        handler: Callable[[str, dict[str, Any]], ToolResult | None] = default_browser,
        answer: Callable[[Event], str | None] | None = None,
        approve: Callable[[Event], bool | None] | None = None,
    ) -> None:
        self.events: list[Event] = []
        self.submit = submit
        self.handler = handler
        # Plays the user: returns the reply to a question (None = no reply).
        self.answer = answer
        self.reply: Callable[[str, str], Any] | None = None
        # Plays the user deciding on a confirmation (None = no decision).
        self.approve = approve
        self.decide: Callable[[str, bool], Any] | None = None
        self.done = asyncio.Event()
        self.tool_started = asyncio.Event()

    async def __call__(self, event: Event) -> None:
        self.events.append(event)
        if event.type == "tool_start":
            self.tool_started.set()
        if event.type == "user_question" and self.answer and self.reply:
            text = self.answer(event)
            if text is not None:
                assert event.request_id
                asyncio.get_running_loop().call_soon(self.reply, event.request_id, text)
        if event.type == "confirmation_request" and self.approve and self.decide:
            approved = self.approve(event)
            if approved is not None:
                assert event.request_id
                asyncio.get_running_loop().call_soon(self.decide, event.request_id, approved)
        if event.type == "tool_start" and self.submit is not None:
            assert event.call_id and event.tool is not None
            result = self.handler(event.tool, event.input or {})
            if result is not None:
                call_id = event.call_id
                asyncio.get_running_loop().call_soon(self.submit, call_id, result)
        if event.type == "task_status" and event.status in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }:
            self.done.set()

    def types(self) -> list[str]:
        return [e.type for e in self.events]
