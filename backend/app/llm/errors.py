"""Provider-agnostic classification of model API errors.

LangChain integrations wrap vendor SDK errors differently (Anthropic and
OpenAI expose `status_code`, Google GenAI exposes `code`, and wrappers keep
the original as `__cause__`), so these helpers walk the exception chain.
"""

from __future__ import annotations

import re

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
_RETRYABLE_TYPES = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "RemoteProtocolError",
    }
)


def _chain(exc: BaseException, limit: int = 6) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < limit and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def error_status(exc: BaseException) -> int | None:
    """The HTTP status behind a provider error, if any."""
    for e in _chain(exc):
        for attr in ("status_code", "code"):
            value = getattr(e, attr, None)
            if isinstance(value, int) and 100 <= value < 600:
                return value
    return None


# Messages providers use when an account or key is out of quota or credit.
_QUOTA_MARKERS = (
    "exceeded your current quota",  # Google, OpenAI
    "insufficient_quota",  # OpenAI
    "credit balance is too low",  # Anthropic
)


def is_quota_exhausted(exc: BaseException) -> bool:
    """The key has used up its quota or credit. Retrying within seconds will
    not help. Per-minute quotas are the exception: those are transient."""
    text = " ".join(str(e) for e in _chain(exc)).lower()
    return any(m in text for m in _QUOTA_MARKERS) and "perminute" not in text


_RETRY_AFTER = (
    re.compile(r"retry in ([0-9.]+)\s*s", re.IGNORECASE),  # Google
    re.compile(r"retryDelay'?\"?:\s*'?\"?([0-9.]+)s", re.IGNORECASE),  # Google RetryInfo
    re.compile(r"try again in ([0-9.]+)\s*s", re.IGNORECASE),  # OpenAI
)


def retry_after_seconds(exc: BaseException) -> float | None:
    """The wait the provider asked for before retrying, when it says."""
    for e in _chain(exc):
        headers = getattr(getattr(e, "response", None), "headers", None)
        value = headers.get("retry-after") if headers is not None else None
        if value:
            try:
                return float(value)
            except ValueError:
                pass
        for pattern in _RETRY_AFTER:
            match = pattern.search(str(e))
            if match:
                return float(match.group(1))
    return None


def is_transient(exc: BaseException) -> bool:
    """Rate limits, overloads, server errors and dropped connections."""
    if is_quota_exhausted(exc):
        return False
    status = error_status(exc)
    if status is not None:
        return status in RETRYABLE_STATUS
    return any(type(e).__name__ in _RETRYABLE_TYPES for e in _chain(exc))
