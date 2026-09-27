"""Streamed task events. Mirrors ServerEvent in shared/protocol.ts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.types import ToolError

EventType = Literal[
    "task_status",
    "agent_thinking",
    "agent_message_delta",
    "agent_message_reset",
    "agent_message",
    "tool_start",
    "tool_result",
    "user_question",
    "user_reply",
    "confirmation_request",
    "confirmation_result",
    "error",
]

# "waiting" means the agent is paused until the user answers.
TaskStatus = Literal[
    "queued", "running", "waiting", "completed", "failed", "cancelled", "interrupted"
]
TaskOutcome = Literal["done", "partial", "blocked"]

TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """One server-to-client event. Only the fields relevant to `type` are set;
    unset fields are omitted on the wire."""

    type: EventType
    task_id: str
    seq: int = 0
    timestamp: datetime = Field(default_factory=utcnow)

    status: TaskStatus | None = None
    message: str | None = None
    message_id: str | None = None
    delta: str | None = None
    call_id: str | None = None
    tool: str | None = None
    input: dict[str, Any] | None = None
    success: bool | None = None
    error: ToolError | None = None
    code: str | None = None
    request_id: str | None = None
    question: str | None = None
    options: list[str] | None = None
    reason: str | None = None
    approved: bool | None = None
    # tool_start: the user approved this call; the extension may run it
    # even though it is high impact.
    confirmed: bool | None = None

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
