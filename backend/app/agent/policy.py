"""What the agent may do without asking, and where.

Enforced here in the backend, before a tool call reaches the browser:

- Domain restrictions: sites outside ALLOWED_DOMAINS (when set) or inside
  BLOCKED_DOMAINS can't be opened, read or used.
- Confirmations: tools whose risk level is listed in CONFIRM_RISK_LEVELS
  need the user's approval before they run. The extension also flags
  individual high-impact actions (buying, sending, deleting...); those
  come back as CONFIRMATION_REQUIRED and are put to the user the same way.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from app.tools.registry import ToolRisk, ToolSpec

# Tools that stay usable while the current page is on a blocked site, so
# the agent can move away from it.
LEAVE_TOOLS = frozenset(
    {"get_current_page", "list_tabs", "switch_tab", "open_tab", "close_tab", "navigate", "wait"}
)
# Tools whose `url` argument opens a site.
URL_TOOLS = frozenset({"navigate", "open_tab"})


def _normalize(domain: str) -> str:
    domain = domain.strip().lower().rstrip(".")
    if "://" in domain:
        domain = urlparse(domain).hostname or ""
    return domain.removeprefix("*.")


def host_of(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    return (parsed.hostname or "").lower().rstrip(".") or None


def _matches(host: str, domains: frozenset[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


class DomainPolicy:
    def __init__(self, allowed: Iterable[str] = (), blocked: Iterable[str] = ()) -> None:
        self._allowed = frozenset(d for d in map(_normalize, allowed) if d)
        self._blocked = frozenset(d for d in map(_normalize, blocked) if d)

    @property
    def restricted(self) -> bool:
        return bool(self._allowed or self._blocked)

    def refusal(self, url: str | None) -> str | None:
        """Why this URL is off limits, or None if it may be used. Non-web
        addresses are left to the extension's own checks."""
        host = host_of(url or "")
        if host is None:
            return None
        if _matches(host, self._blocked):
            return f"{host} is on the list of sites the assistant must not use."
        if self._allowed and not _matches(host, self._allowed):
            return f"{host} is not one of the sites the assistant is allowed to use."
        return None


@dataclass(frozen=True)
class ConfirmationPolicy:
    levels: frozenset[ToolRisk] = frozenset({"high_impact"})

    def requires(self, spec: ToolSpec) -> bool:
        return spec.risk in self.levels
