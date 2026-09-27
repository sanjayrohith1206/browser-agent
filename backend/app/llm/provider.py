"""Provider-neutral model interface built on LangChain chat models.

The agent depends only on LLMProvider. LangChainProvider adapts any LangChain
BaseChatModel (Anthropic, OpenAI, Ollama, ...) to it, so swapping providers is
a configuration change (see app.llm.factory).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    InvalidToolCall,
    ToolCall,
    message_chunk_to_message,
)
from langchain_core.messages.tool import invalid_tool_call

from app.llm.errors import error_status, is_transient, retry_after_seconds
from app.logging import get_logger

log = get_logger(__name__)

MAX_RETRY_WAIT_SECONDS = 60.0


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    OTHER = "other"


@dataclass
class Generation:
    message: AIMessage
    stop_reason: StopReason
    raw_stop_reason: str | None = None

    @property
    def text(self) -> str:
        return message_text(self.message)


class StreamSink(Protocol):
    """Receives incremental output while a model call streams."""

    def on_text_delta(self, delta: str) -> None: ...

    def on_thinking(self, summary: str) -> None: ...

    def on_reset(self) -> None:
        """Discard text streamed so far: the response is being regenerated."""

    def on_tool_call_delta(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """A tool call's arguments so far (partially parsed while streaming)."""


class LLMProvider(Protocol):
    name: str

    async def generate(
        self, messages: Sequence[BaseMessage], *, sink: StreamSink | None = None
    ) -> Generation: ...

    async def generate_with_tools(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, Any]],
        *,
        sink: StreamSink | None = None,
    ) -> Generation: ...


class LangChainProvider:
    """LLMProvider backed by a LangChain chat model, always streaming."""

    def __init__(
        self,
        name: str,
        model: BaseChatModel,
        *,
        drop_schema_keys: frozenset[str] = frozenset(),
        max_attempts: int = 4,
        retry_base_delay: float = 1.0,
    ) -> None:
        self.name = name
        self._model = model
        # JSON Schema keywords the provider rejects or ignores. Arguments are
        # still validated against the full schema server-side.
        self._drop_schema_keys = drop_schema_keys
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay

    async def generate(
        self, messages: Sequence[BaseMessage], *, sink: StreamSink | None = None
    ) -> Generation:
        return await self._stream(self._model, messages, sink)

    async def generate_with_tools(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, Any]],
        *,
        sink: StreamSink | None = None,
    ) -> Generation:
        if not tools:
            raise ValueError("generate_with_tools requires at least one tool")
        if self._drop_schema_keys:
            tools = [_drop_keys(t, self._drop_schema_keys) for t in tools]
        bound = self._model.bind_tools(list(tools))
        return await self._stream(bound, messages, sink)

    async def _stream(
        self, runnable: Any, messages: Sequence[BaseMessage], sink: StreamSink | None
    ) -> Generation:
        """Stream one model response, retrying transient provider failures
        (rate limits, overloads, dropped connections) with backoff. If text
        was already streamed, the sink is told to discard it first, so the
        user never sees a duplicated or half-finished answer."""
        attempt = 0
        while True:
            attempt += 1
            progress = _Progress()
            try:
                return await self._stream_once(runnable, messages, sink, progress)
            except Exception as exc:
                if attempt >= self._max_attempts or not is_transient(exc):
                    raise
                if progress.text_emitted and sink is not None:
                    sink.on_reset()
                delay = self._retry_base_delay * 2 ** (attempt - 1)
                # Honor the provider's requested wait (e.g. per-minute quotas),
                # within reason.
                asked = retry_after_seconds(exc)
                if asked is not None and self._retry_base_delay > 0:
                    delay = max(delay, min(asked, MAX_RETRY_WAIT_SECONDS))
                log.warning(
                    "llm_retry",
                    provider=self.name,
                    attempt=attempt,
                    status=error_status(exc),
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)

    async def _stream_once(
        self,
        runnable: Any,
        messages: Sequence[BaseMessage],
        sink: StreamSink | None,
        progress: _Progress,
    ) -> Generation:
        full: AIMessageChunk | None = None
        thinking: list[str] = []

        def flush_thinking() -> None:
            if sink and thinking:
                summary = "".join(thinking).strip()
                thinking.clear()
                if summary:
                    sink.on_thinking(summary)

        async for chunk in runnable.astream(list(messages)):
            if not isinstance(chunk, AIMessageChunk):
                continue
            full = chunk if full is None else full + chunk
            if sink is None:
                continue
            blocks = chunk.content_blocks
            # Some integrations (Ollama) put reasoning in additional_kwargs
            # instead of content blocks.
            extra_reasoning = chunk.additional_kwargs.get("reasoning_content")
            if isinstance(extra_reasoning, str) and not any(
                b.get("type") == "reasoning" for b in blocks
            ):
                thinking.append(extra_reasoning)
            for block in blocks:
                kind = block.get("type")
                if kind == "reasoning":
                    thinking.append(str(block.get("reasoning") or ""))
                elif kind == "text":
                    text = str(block.get("text") or "")
                    if text:
                        flush_thinking()
                        progress.text_emitted = True
                        sink.on_text_delta(text)
                elif kind in ("tool_call_chunk", "tool_call"):
                    flush_thinking()
                    for call in full.tool_calls:
                        progress.text_emitted = True
                        sink.on_tool_call_delta(call["id"] or "", call["name"], call["args"])
        flush_thinking()

        if full is None:
            raise RuntimeError(f"{self.name}: model returned an empty stream")

        tool_calls, invalid_tool_calls = _strict_tool_calls(full)
        # Convert the whole chunk so provider-specific fields that must be
        # replayed (e.g. Gemini thought signatures, Claude thinking signatures)
        # survive; only the tool calls are replaced with the strict versions.
        message = message_chunk_to_message(full)
        assert isinstance(message, AIMessage)
        message.tool_calls = tool_calls
        message.invalid_tool_calls = invalid_tool_calls
        raw = _raw_stop_reason(full.response_metadata)
        return Generation(
            message=message,
            stop_reason=normalize_stop_reason(
                raw, has_tool_calls=bool(message.tool_calls or message.invalid_tool_calls)
            ),
            raw_stop_reason=raw,
        )


class _Progress:
    text_emitted = False


def _drop_keys(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: _drop_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [_drop_keys(v, keys) for v in value]
    return value


def _strict_tool_calls(full: AIMessageChunk) -> tuple[list[ToolCall], list[InvalidToolCall]]:
    """Re-validate streamed tool arguments with a strict JSON parser.

    LangChain parses streamed arguments leniently (partial JSON), so truncated
    or malformed arguments can surface as a plausible-looking call with
    missing or cut-off values. Browser actions must never run on those; they
    are demoted to invalid calls and the model is asked to retry.
    """
    raw_args = {c.get("id"): c.get("args") for c in full.tool_call_chunks if c.get("id")}
    valid: list[ToolCall] = []
    invalid: list[InvalidToolCall] = list(full.invalid_tool_calls)
    for call in full.tool_calls:
        raw = raw_args.get(call["id"])
        if raw is None or not raw.strip():
            valid.append(call)
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            error = f"arguments are not valid JSON ({exc.msg})"
        else:
            if isinstance(parsed, dict):
                valid.append(call)
                continue
            error = "arguments must be a JSON object"
        invalid.append(invalid_tool_call(name=call["name"], args=raw, id=call["id"], error=error))
    return valid, invalid


def _raw_stop_reason(metadata: dict[str, Any]) -> str | None:
    for key in ("stop_reason", "finish_reason", "done_reason"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def normalize_stop_reason(raw: str | None, *, has_tool_calls: bool) -> StopReason:
    if has_tool_calls:
        return StopReason.TOOL_USE
    match (raw or "").lower():
        case "" | "end_turn" | "stop" | "stop_sequence":
            return StopReason.END_TURN
        case "max_tokens" | "length":
            return StopReason.MAX_TOKENS
        case "refusal" | "content_filter":
            return StopReason.REFUSAL
        case _:
            return StopReason.OTHER


def message_text(message: BaseMessage) -> str:
    """Visible text of a message, across string and content-block formats."""
    if isinstance(message.content, str):
        return message.content
    parts = [str(b.get("text", "")) for b in message.content_blocks if b.get("type") == "text"]
    return "".join(parts)
