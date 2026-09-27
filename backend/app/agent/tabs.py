"""The browser tabs a task works with, as reported by tool results."""

from __future__ import annotations

from typing import Any

from app.tasks.models import TabInfo
from app.tools.types import ToolResult

MAX_TRACKED_TABS = 30


class TabState:
    def __init__(self, tab_id: int | None = None, url: str | None = None, title: str = "") -> None:
        self.current: int | None = tab_id
        self.url: str | None = url
        self.tabs: dict[int, TabInfo] = {}
        if tab_id is not None:
            self.tabs[tab_id] = TabInfo(tab_id=tab_id, url=url or "", title=title)

    def snapshot(self) -> list[TabInfo]:
        return list(self.tabs.values())

    def observe(self, tool: str, result: ToolResult) -> bool:
        """Update from a successful result. Returns True if anything changed."""
        if not result.success or not isinstance(result.result, dict):
            return False
        before = (self.current, self.url, [t.model_dump() for t in self.tabs.values()])
        data: dict[str, Any] = result.result

        if tool == "list_tabs":
            listed = {
                t["tab_id"]: t
                for t in data.get("tabs") or []
                if isinstance(t, dict) and isinstance(t.get("tab_id"), int)
            }
            # Forget tabs that are gone; keep what we know about the rest.
            self.tabs = {
                tab_id: TabInfo(
                    tab_id=tab_id,
                    url=str(t.get("url") or ""),
                    title=str(t.get("title") or ""),
                    opened_by_agent=bool(t.get("opened_by_agent")),
                )
                for tab_id, t in listed.items()
                if tab_id in self.tabs or t.get("opened_by_agent") or t.get("is_task_tab")
            }
        elif tool == "close_tab":
            closed = data.get("closed_tab_id")
            if isinstance(closed, int):
                self.tabs.pop(closed, None)
            current = data.get("current_tab")
            if isinstance(current, dict):
                self._visit(current, opened=False)
        else:
            self._visit(data, opened=tool == "open_tab" or bool(data.get("opened_new_tab")))

        if len(self.tabs) > MAX_TRACKED_TABS:
            for tab_id in list(self.tabs)[: len(self.tabs) - MAX_TRACKED_TABS]:
                if tab_id != self.current:
                    self.tabs.pop(tab_id)
        return before != (self.current, self.url, [t.model_dump() for t in self.tabs.values()])

    def _visit(self, data: dict[str, Any], *, opened: bool) -> None:
        tab_id = data.get("tab_id")
        url = data.get("url")
        if not isinstance(tab_id, int):
            # Same tab; the page may have moved.
            if isinstance(url, str) and url:
                self.url = url
                if self.current in self.tabs:
                    tab = self.tabs[self.current]
                    tab.url = url
                    tab.title = str(data.get("title") or tab.title)
            return
        known = self.tabs.get(tab_id)
        self.tabs[tab_id] = TabInfo(
            tab_id=tab_id,
            url=str(url or (known.url if known else "")),
            title=str(data.get("title") or (known.title if known else "")),
            opened_by_agent=opened or bool(known and known.opened_by_agent),
        )
        self.current = tab_id
        self.url = self.tabs[tab_id].url or None
