from __future__ import annotations

from typing import Any

import pytest

from app.agent.narration import describe_call, describe_result
from app.tools.types import ToolResult


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("navigate", {"url": "https://www.google.com/search?q=x"}, "Opening google.com"),
        ("type_text", {"element_id": "e", "text": "AI news"}, "Typing “AI news”"),
        ("find_element", {"description": "search button"}, "Looking for “search button”"),
        ("select_option", {"element_id": "e", "option": "Price"}, "Choosing “Price”"),
        ("scroll_page", {"direction": "up"}, "Scrolling up"),
        ("wait", {"until_text": "Results"}, "Waiting for “Results” to appear"),
    ],
)
def test_describe_call(tool: str, args: dict[str, Any], expected: str) -> None:
    assert describe_call(tool, args) == expected


def test_describe_call_never_leaks_ids() -> None:
    assert "el_" not in describe_call("click_element", {"element_id": "el_abc_1"})


@pytest.mark.parametrize(
    ("tool", "data", "expected"),
    [
        ("click_element", {"clicked": 'button "Search"'}, "Clicked the “Search” button"),
        (
            "type_text",
            {
                "typed_into": 'searchbox "Search"',
                "submitted": True,
                "navigated": True,
                "title": "AI news - Search",
            },
            "Typed into the “Search” search box and submitted, which opened “AI news - Search”",
        ),
        ("get_elements", {"total": 42}, "Found 42 buttons, links and fields"),
        ("find_element", {"matches": [{"name": "Search", "role": "button"}]}, "Found “Search”"),
        ("find_element", {"matches": []}, "Didn't find a match"),
        (
            "navigate",
            {"url": "https://en.wikipedia.org/wiki/AI", "title": ""},
            "Opened “en.wikipedia.org”",
        ),
        ("wait", {"found": False}, "Waited, but it didn't appear"),
    ],
)
def test_describe_result(tool: str, data: dict[str, Any], expected: str) -> None:
    assert describe_result(tool, ToolResult.ok(tool, data)) == expected


def test_describe_refusals() -> None:
    refused = ToolResult.fail("click_element", "CONFIRMATION_REQUIRED", "purchase")
    assert describe_result("click_element", refused) == "Paused for your approval"
    sensitive = ToolResult.fail("type_text", "SENSITIVE_FIELD", "password")
    assert describe_result("type_text", sensitive) == "Won't type passwords or card details"
