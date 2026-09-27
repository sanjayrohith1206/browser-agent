from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from app.llm.provider import LangChainProvider, StopReason, normalize_stop_reason
from tests.fakes import ScriptedChatModel, text_turn, tool_turn

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_page_content",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


class Recorder:
    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []

    def on_text_delta(self, delta: str) -> None:
        self.log.append(("text", delta))

    def on_thinking(self, summary: str) -> None:
        self.log.append(("thinking", summary))

    def on_reset(self) -> None:
        self.log.append(("reset", ""))

    def on_tool_call_delta(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        self.log.append(("tool", name))


async def test_streams_text_and_flushes_thinking_first() -> None:
    model = ScriptedChatModel(script=[text_turn("Hello there world", reasoning="Plan: greet.")])
    sink = Recorder()
    gen = await LangChainProvider("test", model).generate([HumanMessage("hi")], sink=sink)

    assert sink.log[0] == ("thinking", "Plan: greet.")
    assert "".join(d for k, d in sink.log if k == "text") == "Hello there world"
    assert gen.text == "Hello there world"
    assert gen.stop_reason is StopReason.END_TURN
    assert gen.message.tool_calls == []


async def test_tool_calls_are_parsed() -> None:
    model = ScriptedChatModel(
        script=[tool_turn(("get_page_content", {"max_chars": 5000}), text="Reading.")]
    )
    gen = await LangChainProvider("test", model).generate_with_tools(
        [HumanMessage("summarize")], TOOLS
    )
    assert model.bound_tools == TOOLS
    assert gen.stop_reason is StopReason.TOOL_USE
    assert [(c["name"], c["args"]) for c in gen.message.tool_calls] == [
        ("get_page_content", {"max_chars": 5000})
    ]
    assert gen.text == "Reading."


async def test_generate_with_tools_requires_tools() -> None:
    provider = LangChainProvider("test", ScriptedChatModel(script=[]))
    with pytest.raises(ValueError):
        await provider.generate_with_tools([HumanMessage("x")], [])


@pytest.mark.parametrize(
    ("raw", "has_tools", "expected"),
    [
        ("end_turn", False, StopReason.END_TURN),
        ("stop", False, StopReason.END_TURN),
        (None, False, StopReason.END_TURN),
        ("max_tokens", False, StopReason.MAX_TOKENS),
        ("length", False, StopReason.MAX_TOKENS),
        ("refusal", False, StopReason.REFUSAL),
        ("content_filter", False, StopReason.REFUSAL),
        ("tool_calls", True, StopReason.TOOL_USE),
        ("end_turn", True, StopReason.TOOL_USE),
        ("pause_turn", False, StopReason.OTHER),
    ],
)
def test_normalize_stop_reason(raw: str | None, has_tools: bool, expected: StopReason) -> None:
    assert normalize_stop_reason(raw, has_tool_calls=has_tools) is expected


class Overloaded(Exception):
    code = 503


class BadRequest(Exception):
    status_code = 400


async def test_retries_transient_errors_before_output() -> None:
    model = ScriptedChatModel(
        script=[text_turn("Recovered")], transient_errors=[Overloaded(), Overloaded()]
    )
    provider = LangChainProvider("test", model, retry_base_delay=0)
    gen = await provider.generate([HumanMessage("hi")], sink=Recorder())
    assert gen.text == "Recovered"
    assert len(model.requests) == 3


async def test_gives_up_after_max_attempts_and_on_permanent_errors() -> None:
    model = ScriptedChatModel(script=[], transient_errors=[Overloaded()] * 4)
    with pytest.raises(Overloaded):
        await LangChainProvider("test", model, retry_base_delay=0).generate([HumanMessage("x")])
    assert len(model.requests) == 4

    model = ScriptedChatModel(script=[], transient_errors=[BadRequest()])
    with pytest.raises(BadRequest):
        await LangChainProvider("test", model, retry_base_delay=0).generate([HumanMessage("x")])
    assert len(model.requests) == 1


async def test_drops_unsupported_schema_keys() -> None:
    model = ScriptedChatModel(script=[tool_turn(("get_page_content", {}))])
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_page_content",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "object", "additionalProperties": False}},
                    "additionalProperties": False,
                },
            },
        }
    ]
    provider = LangChainProvider(
        "google", model, drop_schema_keys=frozenset({"additionalProperties"})
    )
    await provider.generate_with_tools([HumanMessage("x")], tools)
    assert "additionalProperties" not in str(model.bound_tools)
    assert "additionalProperties" in str(tools)  # caller's definitions untouched


async def test_midstream_failure_resets_then_retries() -> None:
    model = ScriptedChatModel(
        script=[text_turn("Solar panels convert light")], midstream_errors=[Overloaded()]
    )
    sink = Recorder()
    gen = await LangChainProvider("test", model, retry_base_delay=0).generate(
        [HumanMessage("hi")], sink=sink
    )
    assert gen.text == "Solar panels convert light"
    kinds = [k for k, _ in sink.log]
    assert kinds[0] == "text" and kinds[1] == "reset"
    after_reset = "".join(d for k, d in sink.log[2:] if k == "text")
    assert after_reset == "Solar panels convert light"
