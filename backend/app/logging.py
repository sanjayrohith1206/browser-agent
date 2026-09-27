"""Structured logging setup."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Log fields that must never reach the logs, whatever their value.
_SECRET_KEYS = frozenset(
    {"password", "token", "authorization", "api_key", "llm_api_key", "secret", "cookie"}
)


def _redact(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event:
        if key.lower() in _SECRET_KEYS:
            event[key] = "[redacted]"
    return event


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _redact,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    # google-genai warns about automatic function calling on every streamed
    # call; LangChain drives tool calls itself, so the warning is noise.
    logging.getLogger("google_genai.models").setLevel(logging.ERROR)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
