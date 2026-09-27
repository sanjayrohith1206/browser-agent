"""Task domain models. Mirrors Task in shared/protocol.ts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agent.events import TaskOutcome, TaskStatus, utcnow
from app.tools.types import ToolError

MAX_PROMPT_CHARS = 8000


class PageContext(BaseModel):
    tab_id: int
    url: str = Field(max_length=8192)
    title: str = Field(default="", max_length=2048)


class CreateTaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    page: PageContext | None = None

    @field_validator("prompt")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt must not be blank")
        return v


class TabInfo(BaseModel):
    """A browser tab the task has worked with."""

    tab_id: int
    url: str = ""
    title: str = ""
    opened_by_agent: bool = False


class TaskStep(BaseModel):
    call_id: str
    tool: str
    input: dict[str, Any]
    tab_id: int | None = None
    success: bool | None = None
    message: str | None = None
    error: ToolError | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class TaskSummary(BaseModel):
    """A task as listed in history (no steps)."""

    id: str
    goal: str
    status: TaskStatus
    outcome: TaskOutcome | None = None
    current_url: str | None = None
    created_at: datetime
    updated_at: datetime


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str
    status: TaskStatus = "queued"
    page: PageContext | None = None
    current_url: str | None = None
    current_tab: int | None = None
    tabs: list[TabInfo] = Field(default_factory=list)
    steps: list[TaskStep] = Field(default_factory=list)
    result: str | None = None
    error: str | None = None
    outcome: TaskOutcome | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
