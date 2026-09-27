"""Recovery guidance for the model.

Tool results already say what went wrong; the hints here say what to do
next. They come from how real runs got stuck: re-using element IDs after
the page changed, repeating a failing call, re-reading a page that had not
changed, and running out of steps without an answer.
"""

from __future__ import annotations

import json
from typing import Any

from app.tools.types import ToolResult

_ERROR_HINTS: dict[str, str] = {
    "ELEMENT_NOT_FOUND": (
        "Element IDs stop working when the page changes. Get fresh IDs with "
        "find_element or get_elements, then retry."
    ),
    "ELEMENT_NOT_INTERACTABLE": (
        "Try the visible control that does the same thing, scroll it into view, or open "
        "the menu or tab that contains it first."
    ),
    "OPTION_NOT_FOUND": (
        "Choose the listed option closest to what the user asked for. If none fits, "
        "ask_user."
    ),
    "TIMEOUT": (
        "The page may still be loading. Use wait, check where you are with "
        "get_current_page, then retry once."
    ),
    "CONTENT_SCRIPT_UNAVAILABLE": (
        "The page did not answer. Wait briefly and retry once; if it fails again, "
        "navigate to the current URL to reload it."
    ),
    "EXECUTION_FAILED": (
        "Re-check the page state (get_current_page, find_element) and try a different "
        "approach rather than the same call."
    ),
    "NAVIGATION_FAILED": (
        "Check the address. Try the site's home page, or search for the page instead."
    ),
    "INVALID_URL": "Use a full address that starts with https://.",
    "PAGE_NOT_SCRIPTABLE": (
        "Browser pages like this can't be read or used. Navigate to a website, or tell "
        "the user."
    ),
    "TAB_NOT_FOUND": (
        "That tab was closed. Use list_tabs to see what is open, then switch_tab or "
        "open_tab."
    ),
    "TAB_NOT_VISIBLE": "Use get_page_content or get_elements instead of a screenshot.",
    "SENSITIVE_FIELD": (
        "Never type passwords or card details. Ask the user to fill in this field "
        "themselves (ask_user), then continue."
    ),
    "INVALID_INPUT": "Fix the arguments so they match the tool's parameters.",
    "USER_DECLINED": (
        "The user said no. Don't retry or work around it: ask what they would like "
        "instead, or finish and say what was left undone."
    ),
    "DOMAIN_BLOCKED": (
        "This site is off limits. Use a different site, or tell the user it can't be used."
    ),
    "UNKNOWN_TOOL": "Use only the tools you have been given.",
}

# Tools that only observe; repeating one without acting in between returns
# the same thing.
_OBSERVE_TOOLS = frozenset(
    {"get_current_page", "get_page_content", "get_elements", "find_element", "get_links"}
)
REPEAT_FAILURE_THRESHOLD = 2
CONSECUTIVE_FAILURE_THRESHOLD = 3
WRAP_UP_STEPS = 2


def _signature(tool: str, args: dict[str, Any]) -> str:
    return f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"


class RecoveryTracker:
    """Watches one task's tool calls and adds hints to their results."""

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._consecutive_failures = 0
        self._observed_since_action: set[str] = set()

    def advise(self, tool: str, args: dict[str, Any], result: ToolResult) -> ToolResult:
        signature = _signature(tool, args)
        hints: list[str] = []

        if result.success:
            self._consecutive_failures = 0
            if tool in _OBSERVE_TOOLS:
                if signature in self._observed_since_action:
                    hints.append(
                        "You already made this exact call and nothing has changed on the page "
                        "since. Use the result you have and move on."
                    )
                self._observed_since_action.add(signature)
            else:
                # Anything else may change the page.
                self._observed_since_action.clear()
        else:
            self._consecutive_failures += 1
            count = self._failures.get(signature, 0) + 1
            self._failures[signature] = count
            code = result.error.code if result.error else ""
            if code in _ERROR_HINTS:
                hints.append(_ERROR_HINTS[code])
            if count >= REPEAT_FAILURE_THRESHOLD:
                hints.append(
                    f"This exact call has now failed {count} times. Do not repeat it: take a "
                    "different approach, ask the user, or finish with what you have."
                )
            elif self._consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
                hints.append(
                    f"{self._consecutive_failures} steps in a row have failed. Step back: "
                    "check where you are, reconsider the plan, and try another route."
                )

        if not hints:
            return result
        return result.model_copy(update={"hint": " ".join(hints)})


def budget_note(steps_taken: int, max_steps: int) -> str | None:
    """A reminder for the model as it nears the step limit."""
    remaining = max_steps - steps_taken
    if remaining <= 0:
        return (
            "You have no steps left. Call finish_task now with what you have found and "
            "what is left for the user to do."
        )
    if remaining <= WRAP_UP_STEPS:
        plural = "s" if remaining > 1 else ""
        return (
            f"Only {remaining} step{plural} left. Wrap up: finish the most important part, "
            "then call finish_task."
        )
    return None
