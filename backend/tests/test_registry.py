from __future__ import annotations

from app.tools.registry import ToolRegistry


def test_loads_shared_definitions(registry: ToolRegistry) -> None:
    assert registry.names == [
        "get_current_page",
        "get_page_content",
        "get_elements",
        "find_element",
        "get_links",
        "click_element",
        "type_text",
        "clear_input",
        "select_option",
        "scroll_page",
        "navigate",
        "wait",
        "take_screenshot",
        "list_tabs",
        "open_tab",
        "switch_tab",
        "close_tab",
        "ask_user",
        "finish_task",
    ]
    risks = {n: registry.get(n).risk for n in registry.names}  # type: ignore[union-attr]
    assert risks["get_elements"] == "read" and risks["click_element"] == "interact"
    assert risks["navigate"] == "interact" and risks["scroll_page"] == "read"
    runners = {n: registry.get(n).runs_in for n in registry.names}  # type: ignore[union-attr]
    assert runners["ask_user"] == runners["finish_task"] == "agent"
    assert runners["click_element"] == "browser"


def test_validates_arguments(registry: ToolRegistry) -> None:
    assert registry.validate("get_current_page", {}) == []
    assert registry.validate("get_page_content", {"max_chars": 5000}) == []

    errors = registry.validate("get_page_content", {"max_chars": 10})
    assert errors and errors[0].startswith("max_chars:")

    assert registry.validate("get_current_page", {"extra": 1})
    assert registry.validate("get_page_content", {"max_chars": "lots"})
    assert registry.validate("nope", {}) == ["unknown tool 'nope'"]


def test_langchain_tool_format(registry: ToolRegistry) -> None:
    tools = registry.as_langchain_tools()
    assert {t["function"]["name"] for t in tools} == set(registry.names)
    for t in tools:
        assert t["type"] == "function"
        assert t["function"]["parameters"]["type"] == "object"
        assert t["function"]["description"]


def test_phase2_argument_validation(registry: ToolRegistry) -> None:
    assert registry.validate("click_element", {"element_id": "el_abc_1"}) == []
    assert registry.validate("click_element", {})  # element_id required
    assert (
        registry.validate("type_text", {"element_id": "e", "text": "hi", "press_enter": True}) == []
    )
    assert registry.validate("type_text", {"element_id": "e"})  # text required
    assert registry.validate("scroll_page", {"direction": "sideways"})
    assert registry.validate("wait", {"seconds": 60})  # above maximum
    assert registry.validate("navigate", {"url": ""})
    assert registry.validate("find_element", {"description": "search box", "role": "textbox"}) == []


def test_phase3_argument_validation(registry: ToolRegistry) -> None:
    assert registry.validate("ask_user", {"question": "Which size?", "options": ["S", "M"]}) == []
    assert registry.validate("ask_user", {"question": ""})
    assert registry.validate("ask_user", {"question": "?", "options": ["a"] * 7})
    assert registry.validate("finish_task", {"answer": "Done.", "outcome": "done"}) == []
    assert registry.validate("finish_task", {"answer": "Done.", "outcome": "great"})
    assert registry.validate("finish_task", {"outcome": "done"})
