"""Plain-language descriptions of tool activity for the user-facing timeline.
Users should never see tool names or element IDs."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.tools.types import ToolResult


def _quote(text: Any, limit: int = 40) -> str:
    s = " ".join(str(text or "").split())
    return f"“{s[: limit - 1]}…”" if len(s) > limit else f"“{s}”"


def _site(url: Any) -> str:
    host = urlparse(str(url or "")).netloc
    return host.removeprefix("www.") or "the page"


def describe_call(tool: str, args: dict[str, Any]) -> str:
    match tool:
        case "get_current_page":
            return "Checking the current page"
        case "get_page_content":
            return "Reading the page"
        case "get_elements":
            return "Looking at the page's buttons and fields"
        case "find_element":
            return f"Looking for {_quote(args.get('description'))}"
        case "get_links":
            return "Collecting the links on the page"
        case "click_element":
            return "Clicking"
        case "type_text":
            return f"Typing {_quote(args.get('text'))}"
        case "clear_input":
            return "Clearing a field"
        case "select_option":
            return f"Choosing {_quote(args.get('option'))}"
        case "scroll_page":
            if args.get("element_id"):
                return "Scrolling to an element"
            return f"Scrolling {args.get('direction') or 'down'}"
        case "navigate":
            return f"Opening {_site(args.get('url'))}"
        case "wait":
            if args.get("until_text"):
                return f"Waiting for {_quote(args.get('until_text'))} to appear"
            return "Waiting for the page"
        case "take_screenshot":
            return "Taking a screenshot"
        case "list_tabs":
            return "Checking the open tabs"
        case "open_tab":
            return f"Opening {_site(args.get('url'))} in a new tab"
        case "switch_tab":
            return "Switching tabs"
        case "close_tab":
            return "Closing a tab"
        case _:
            return "Working in the browser"


_FAILURES = {
    "PAGE_NOT_SCRIPTABLE": "This page can't be used by extensions",
    "TAB_NOT_FOUND": "The tab was closed",
    "TIMEOUT": "The browser took too long to respond",
    "BROWSER_DISCONNECTED": "Lost connection to the browser",
    "ELEMENT_NOT_FOUND": "That part of the page is gone",
    "ELEMENT_NOT_INTERACTABLE": "That part of the page can't be used",
    "OPTION_NOT_FOUND": "That option isn't available",
    "CONFIRMATION_REQUIRED": "Paused for your approval",
    "SENSITIVE_FIELD": "Won't type passwords or card details",
    "INVALID_URL": "That address isn't valid",
    "NAVIGATION_FAILED": "The page couldn't be opened",
    "TAB_NOT_VISIBLE": "The tab isn't visible, so no screenshot",
    "DOMAIN_BLOCKED": "That site is off limits",
    "USER_DECLINED": "You said no, so it wasn't done",
}


def describe_result(tool: str, result: ToolResult) -> str:
    if not result.success:
        code = result.error.code if result.error else None
        return _FAILURES.get(code or "", "That step didn't work")

    data: dict[str, Any] = result.result if isinstance(result.result, dict) else {}
    match tool:
        case "get_current_page":
            return f"On {_quote(data.get('title') or data.get('url') or 'the page', 60)}"
        case "get_page_content":
            title = _quote(data.get("title") or "the page", 60)
            chars = data.get("total_chars")
            return (
                f"Read {title} ({chars:,} characters)"
                if isinstance(chars, int)
                else f"Read {title}"
            )
        case "get_elements":
            return f"Found {data.get('total', 0)} buttons, links and fields"
        case "find_element":
            matches = data.get("matches") or []
            if not matches:
                return "Didn't find a match"
            best = matches[0]
            return f"Found {_quote(best.get('name') or best.get('text') or best.get('role'))}"
        case "get_links":
            return f"Found {data.get('total', 0)} links"
        case "click_element" | "type_text" | "clear_input" | "select_option":
            base = {
                "click_element": f"Clicked {_label(data.get('clicked'))}",
                "type_text": f"Typed into {_label(data.get('typed_into'))}"
                + (" and submitted" if data.get("submitted") else ""),
                "clear_input": f"Cleared {_label(data.get('typed_into'))}",
                "select_option": f"Chose {_quote(data.get('selected'))}",
            }[tool]
            if data.get("navigated"):
                opened = _quote(data.get("title") or _site(data.get("url")), 50)
                return f"{base}, which opened {opened}"
            return base
        case "scroll_page":
            return "Reached the bottom of the page" if data.get("at_bottom") else "Scrolled"
        case "navigate":
            return f"Opened {_quote(data.get('title') or _site(data.get('url')), 60)}"
        case "wait":
            if data.get("found") is False:
                return "Waited, but it didn't appear"
            return "Done waiting"
        case "take_screenshot":
            return "Took a screenshot"
        case "list_tabs":
            count = len(data.get("tabs") or [])
            return f"Found {count} open tab{'s' if count != 1 else ''}"
        case "open_tab" | "switch_tab":
            verb = "Opened" if tool == "open_tab" else "Switched to"
            return f"{verb} {_quote(data.get('title') or _site(data.get('url')), 60)}"
        case "close_tab":
            return "Closed the tab"
        case _:
            return "Done"


def _label(described: Any) -> str:
    """'button "Search"' -> 'the “Search” button'."""
    text = str(described or "").strip()
    if '"' in text:
        role, _, rest = text.partition(" ")
        name = rest.strip('"')
        noun = {"textbox": "field", "searchbox": "search box", "combobox": "field"}.get(role, role)
        return f"the {_quote(name)} {noun}"
    return f"the {text}" if text else "it"
