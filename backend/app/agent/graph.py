"""The browser agent loop, as a LangGraph state graph.

    START -> agent --(tool calls)--> tools --> agent --> ... --> END

The agent node asks the model for the next step given everything observed so
far; the tools node executes the requested tools and feeds structured
results back. The model decides every next step from the latest
observations; there is no fixed sequence.

Most tools run in the browser (through the bridge). Two run here: ask_user
pauses for the user's answer, and finish_task ends the run with the final
answer. A plain text reply with no tool call also ends the run.

Before a browser tool runs, the action policy applies: sites outside the
allowed domains are refused, and high-impact calls wait for the user's
approval (see app.agent.policy).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.agent.events import TaskOutcome
from app.agent.interaction import UserInteraction
from app.agent.narration import describe_call, describe_result
from app.agent.policy import LEAVE_TOOLS, URL_TOOLS, ConfirmationPolicy, DomainPolicy
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.recovery import RecoveryTracker, budget_note
from app.agent.tabs import TabState
from app.browser.bridge import BrowserBridge
from app.llm.provider import LLMProvider, StopReason
from app.logging import get_logger
from app.tools.registry import ToolRegistry
from app.tools.types import ToolResult

log = get_logger(__name__)

FINISH_TOOL = "finish_task"
ASK_TOOL = "ask_user"


class Publish(Protocol):
    def __call__(self, type: str, **fields: Any) -> None: ...


class StepRecorder(Protocol):
    async def step_started(
        self, call_id: str, tool: str, args: dict[str, Any], tab_id: int | None
    ) -> None: ...

    async def step_finished(self, call_id: str, result: ToolResult, message: str) -> None: ...

    async def tabs_changed(self, tabs: TabState) -> None: ...


@dataclass(frozen=True)
class ToolLimits:
    """Caps on how much page tools return (keeps small context windows safe),
    and on how many tool steps a task may take."""

    page_text: int = 40000
    elements: int = 150
    max_steps: int = 25


@dataclass
class AgentContext:
    task_id: str
    provider: LLMProvider
    registry: ToolRegistry
    bridge: BrowserBridge
    publish: Publish
    recorder: StepRecorder
    interaction: UserInteraction
    limits: ToolLimits = field(default_factory=lambda: ToolLimits())
    recovery: RecoveryTracker = field(default_factory=RecoveryTracker)
    tabs: TabState = field(default_factory=TabState)
    domains: DomainPolicy = field(default_factory=DomainPolicy)
    confirmations: ConfirmationPolicy = field(default_factory=ConfirmationPolicy)


class Finish(TypedDict):
    answer: str
    outcome: TaskOutcome


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    stop_reason: StopReason
    raw_stop_reason: str | None
    steps: int
    finish: Finish | None
    step_limit_reached: bool


def final_message_id(call_id: str) -> str:
    """The chat message that carries a finish_task answer."""
    return f"final_{call_id}"


class _StreamToEvents:
    """Forwards model output to the task event stream, including the answer
    of a finish_task call as it is being written."""

    def __init__(self, publish: Publish, message_id: str) -> None:
        self._publish = publish
        self._message_id = message_id
        self._answers: dict[str, str] = {}

    def on_text_delta(self, delta: str) -> None:
        self._publish("agent_message_delta", message_id=self._message_id, delta=delta)

    def on_thinking(self, summary: str) -> None:
        self._publish("agent_thinking", message=summary)

    def on_reset(self) -> None:
        self._publish("agent_message_reset", message_id=self._message_id)
        for call_id in self._answers:
            self._publish("agent_message_reset", message_id=final_message_id(call_id))
        self._answers.clear()

    def on_tool_call_delta(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        answer = args.get("answer")
        if name != FINISH_TOOL or not call_id or not isinstance(answer, str):
            return
        sent = self._answers.get(call_id, "")
        # Partial JSON can briefly drop a trailing escape; only stream growth.
        if len(answer) > len(sent) and answer.startswith(sent):
            self._publish(
                "agent_message_delta",
                message_id=final_message_id(call_id),
                delta=answer[len(sent) :],
            )
            self._answers[call_id] = answer


async def call_model(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    ctx = runtime.context
    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    generation = await ctx.provider.generate_with_tools(
        [SystemMessage(SYSTEM_PROMPT), *state["messages"]],
        ctx.registry.as_langchain_tools(),
        sink=_StreamToEvents(ctx.publish, message_id),
    )
    text = generation.text.strip()
    if text:
        ctx.publish("agent_message", message_id=message_id, message=text)
    log.info(
        "model_step",
        task_id=ctx.task_id,
        stop_reason=generation.stop_reason.value,
        tool_calls=[c["name"] for c in generation.message.tool_calls],
        usage=generation.message.usage_metadata,
    )
    return {
        "messages": [generation.message],
        "stop_reason": generation.stop_reason,
        "raw_stop_reason": generation.raw_stop_reason,
    }


async def run_tools(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    ctx = runtime.context
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    replies: list[ToolMessage] = []
    steps = state.get("steps", 0)
    out_of_steps = steps >= ctx.limits.max_steps

    # Calls whose arguments could not be parsed still need an answer so the
    # model can correct itself.
    for bad in last.invalid_tool_calls:
        result = ToolResult.fail(
            bad.get("name") or "unknown",
            "INVALID_INPUT",
            f"The tool call was rejected: {bad.get('error') or 'arguments could not be parsed'}."
            " Retry with a valid JSON object.",
        )
        replies.append(_tool_message(bad.get("id") or "", result))

    # Tools run in order: browser actions depend on each other, and nothing
    # runs after finish_task.
    images: list[HumanMessage] = []
    finish: Finish | None = None
    for call in last.tool_calls:
        name, args, call_id = call["name"], call["args"], call["id"] or ""
        if finish is not None:
            result = ToolResult.fail(name, "NOT_RUN", "Not run: the task had already finished.")
        elif name == FINISH_TOOL:
            result, finish = _finish(ctx, call_id, args)
        elif out_of_steps:
            result = ToolResult.fail(
                name, "STEP_LIMIT", "Not run: the task has used all of its steps."
            )
        else:
            result = await _execute(ctx, name, args)
            if name != ASK_TOOL:
                result = ctx.recovery.advise(name, args, result)
        result, image = _extract_image(result)
        replies.append(_tool_message(call_id, result))
        if image is not None:
            images.append(image)

    steps += 1
    note = budget_note(steps, ctx.limits.max_steps) if finish is None else None
    if note and replies:
        replies[-1] = _with_note(replies[-1], note)

    # Images go in a user message after all tool results: every provider
    # accepts that, while image content inside tool results is not portable.
    update: AgentState = {"messages": [*replies, *images], "steps": steps}
    if finish is not None:
        update["finish"] = finish
    elif out_of_steps:
        update["step_limit_reached"] = True
    return update


def _finish(
    ctx: AgentContext, call_id: str, args: dict[str, Any]
) -> tuple[ToolResult, Finish | None]:
    errors = ctx.registry.validate(FINISH_TOOL, args)
    if errors:
        return ToolResult.fail(FINISH_TOOL, "INVALID_INPUT", "; ".join(errors)), None
    answer = str(args["answer"]).strip()
    ctx.publish("agent_message", message_id=final_message_id(call_id), message=answer)
    return ToolResult.ok(FINISH_TOOL, {"finished": True}), {
        "answer": answer,
        "outcome": args["outcome"],
    }


MAX_IMAGE_BYTES = 5 * 1024 * 1024
_DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$")


def _extract_image(result: ToolResult) -> tuple[ToolResult, HumanMessage | None]:
    """Move a screenshot out of the JSON tool result into an image message."""
    data = result.result
    if not (result.success and isinstance(data, dict) and "image_data_url" in data):
        return result, None
    rest = {k: v for k, v in data.items() if k != "image_data_url"}
    match = _DATA_URL.match(str(data["image_data_url"]))
    if match is None or len(match.group(2)) * 3 // 4 > MAX_IMAGE_BYTES:
        return ToolResult.fail(
            result.tool, "EXECUTION_FAILED", "The screenshot was unusable."
        ), None
    rest["image"] = "The screenshot is attached in the next message."
    image = HumanMessage(
        content=[
            {"type": "text", "text": f"Screenshot of {rest.get('url') or 'the page'}:"},
            {"type": "image", "base64": match.group(2), "mime_type": match.group(1)},
        ]
    )
    return result.model_copy(update={"result": rest}), image


async def _execute(ctx: AgentContext, tool: str, args: dict[str, Any]) -> ToolResult:
    spec = ctx.registry.get(tool)
    if spec is None:
        return ToolResult.fail(tool, "UNKNOWN_TOOL", f"There is no tool named {tool!r}.")
    errors = ctx.registry.validate(tool, args)
    if errors:
        return ToolResult.fail(tool, "INVALID_INPUT", "; ".join(errors))
    if tool == ASK_TOOL:
        return await _ask_user(ctx, args)

    refusal = _domain_refusal(ctx, tool, args)
    if refusal:
        return ToolResult.fail(tool, "DOMAIN_BLOCKED", refusal)

    args = _within_budget(tool, args, ctx.limits)
    confirmed = False
    if ctx.confirmations.requires(spec):
        confirmed = await ctx.interaction.confirm(
            describe_call(tool, args), "This step always needs your approval."
        )
        if not confirmed:
            return _declined(tool)

    result = await _run_in_browser(ctx, tool, args, confirmed=confirmed)
    error = result.error
    if not confirmed and error is not None and error.code == "CONFIRMATION_REQUIRED":
        # The extension recognized a high-impact action: put it to the user.
        details = error.details or {}
        action = details.get("action") or describe_call(tool, args)
        reason = details.get("reason")
        approved = await ctx.interaction.confirm(
            action, f"This would {reason}." if reason else error.message
        )
        if not approved:
            return _declined(tool)
        result = await _run_in_browser(ctx, tool, args, confirmed=True)

    if ctx.tabs.observe(tool, result):
        await ctx.recorder.tabs_changed(ctx.tabs)
    return result


async def _run_in_browser(
    ctx: AgentContext, tool: str, args: dict[str, Any], *, confirmed: bool
) -> ToolResult:
    call_id = f"call_{uuid.uuid4().hex[:16]}"
    await ctx.recorder.step_started(call_id, tool, args, ctx.tabs.current)
    result = await ctx.bridge.call(call_id, tool, args, confirmed=confirmed)
    # The extension reports its own tool name; trust the one we asked for.
    result = result.model_copy(update={"tool": tool})
    message = describe_result(tool, result)
    ctx.publish(
        "tool_result",
        call_id=call_id,
        tool=tool,
        success=result.success,
        message=message,
        error=result.error,
    )
    await ctx.recorder.step_finished(call_id, result, message)
    return result


def _domain_refusal(ctx: AgentContext, tool: str, args: dict[str, Any]) -> str | None:
    if tool in URL_TOOLS:
        return ctx.domains.refusal(str(args.get("url") or ""))
    if tool not in LEAVE_TOOLS:
        refusal = ctx.domains.refusal(ctx.tabs.url)
        if refusal:
            return f"The current page can't be used: {refusal} Navigate elsewhere first."
    return None


def _declined(tool: str) -> ToolResult:
    return ToolResult.fail(
        tool,
        "USER_DECLINED",
        "The user declined this action, so it was not done. Do not try it again or work "
        "around it; ask what they would like instead, or finish.",
    )


async def _ask_user(ctx: AgentContext, args: dict[str, Any]) -> ToolResult:
    answer = await ctx.interaction.ask(str(args["question"]), list(args.get("options") or []))
    if answer is None:
        return ToolResult.fail(
            ASK_TOOL,
            "TIMEOUT",
            "The user did not answer. Continue without it if you reasonably can; otherwise "
            "finish and explain what you need from them.",
        )
    return ToolResult.ok(ASK_TOOL, {"answer": answer})


_LIMITED_ARGS = {
    "get_page_content": ("max_chars", "page_text"),
    "get_elements": ("max_elements", "elements"),
    "get_links": ("max_links", "elements"),
}


def _within_budget(tool: str, args: dict[str, Any], limits: ToolLimits) -> dict[str, Any]:
    if tool not in _LIMITED_ARGS:
        return args
    arg, limit_name = _LIMITED_ARGS[tool]
    limit: int = getattr(limits, limit_name)
    requested = args.get(arg)
    value = min(requested, limit) if isinstance(requested, int) else limit
    return {**args, arg: value}


def _tool_message(call_id: str, result: ToolResult) -> ToolMessage:
    return ToolMessage(
        content=result.model_dump_json(exclude_none=True),
        tool_call_id=call_id,
        name=result.tool,
        status="success" if result.success else "error",
    )


def _with_note(message: ToolMessage, note: str) -> ToolMessage:
    data = json.loads(str(message.content))
    data["system_note"] = note
    return message.model_copy(update={"content": json.dumps(data)})


def route_after_model(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and (last.tool_calls or last.invalid_tool_calls):
        return "tools"
    return END


def route_after_tools(state: AgentState) -> str:
    if state.get("finish") or state.get("step_limit_reached"):
        return END
    return "agent"


def build_graph() -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("agent", call_model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_model, ["tools", END])
    graph.add_conditional_edges("tools", route_after_tools, ["agent", END])
    return graph.compile()


def recursion_limit(max_steps: int) -> int:
    """LangGraph counts node executions; each step is agent + tools. The step
    budget normally ends the run first; this is a backstop, with room for
    the final wrap-up turn."""
    return (max_steps + 2) * 2 + 1
