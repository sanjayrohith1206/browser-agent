from __future__ import annotations

from app.agent.recovery import RecoveryTracker, budget_note
from app.tools.types import ToolResult


def test_repeated_observation_without_action_is_flagged() -> None:
    tracker = RecoveryTracker()
    ok = ToolResult.ok("get_elements", {"elements": []})
    assert tracker.advise("get_elements", {}, ok).hint is None
    assert "already made this exact call" in (tracker.advise("get_elements", {}, ok).hint or "")

    # An action in between may change the page, so observing again is fine.
    tracker.advise("click_element", {"element_id": "e1"}, ToolResult.ok("click_element", {}))
    assert tracker.advise("get_elements", {}, ok).hint is None


def test_consecutive_failures_suggest_stepping_back() -> None:
    tracker = RecoveryTracker()
    hints = [
        tracker.advise(
            "click_element",
            {"element_id": f"e{i}"},
            ToolResult.fail("click_element", "EXECUTION_FAILED", "boom"),
        ).hint
        or ""
        for i in range(3)
    ]
    assert "Step back" not in hints[1]
    assert "3 steps in a row have failed" in hints[2]


def test_success_resets_the_failure_streak() -> None:
    tracker = RecoveryTracker()
    fail = ToolResult.fail("navigate", "NAVIGATION_FAILED", "no")
    tracker.advise("navigate", {"url": "https://a.example"}, fail)
    tracker.advise("navigate", {"url": "https://b.example"}, fail)
    tracker.advise("get_current_page", {}, ToolResult.ok("get_current_page", {}))
    hint = tracker.advise("navigate", {"url": "https://c.example"}, fail).hint or ""
    assert "in a row" not in hint and "address" in hint


def test_budget_note() -> None:
    assert budget_note(1, 25) is None
    assert budget_note(23, 25) == (
        "Only 2 steps left. Wrap up: finish the most important part, then call finish_task."
    )
    assert "no steps left" in (budget_note(25, 25) or "")
