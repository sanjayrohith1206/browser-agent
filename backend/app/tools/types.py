"""Structured tool results. Mirrors ToolResult in shared/protocol.ts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ToolErrorCode = Literal[
    "INVALID_INPUT",
    "UNKNOWN_TOOL",
    "TAB_NOT_FOUND",
    "PAGE_NOT_SCRIPTABLE",
    "CONTENT_SCRIPT_UNAVAILABLE",
    "TIMEOUT",
    "EXECUTION_FAILED",
    "BROWSER_DISCONNECTED",
    "ELEMENT_NOT_FOUND",
    "ELEMENT_NOT_INTERACTABLE",
    "OPTION_NOT_FOUND",
    "CONFIRMATION_REQUIRED",
    "SENSITIVE_FIELD",
    "INVALID_URL",
    "NAVIGATION_FAILED",
    "TAB_NOT_VISIBLE",
    "STEP_LIMIT",
    "NOT_RUN",
    "DOMAIN_BLOCKED",
    "USER_DECLINED",
]


class ToolError(BaseModel):
    code: ToolErrorCode
    message: str
    # CONFIRMATION_REQUIRED: {"action": ..., "reason": ...} from the extension.
    details: dict[str, str] | None = None


class ToolResult(BaseModel):
    success: bool
    tool: str
    result: Any = None
    error: ToolError | None = None
    # Guidance for the model on how to recover, added by the agent loop.
    hint: str | None = None

    @classmethod
    def ok(cls, tool: str, result: Any) -> ToolResult:
        return cls(success=True, tool=tool, result=result)

    @classmethod
    def fail(cls, tool: str, code: ToolErrorCode, message: str) -> ToolResult:
        return cls(success=False, tool=tool, error=ToolError(code=code, message=message))
