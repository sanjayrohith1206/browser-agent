"""The configured Claude request, as LangChain would send it (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings
from app.llm.factory import ANTHROPIC_FALLBACK_BETA, ProviderConfigError, build_chat_model
from app.tools.registry import ToolRegistry


def payload(settings: Settings, registry: ToolRegistry) -> dict[str, Any]:
    bound = build_chat_model(settings).bind_tools(registry.as_langchain_tools())
    return bound.bound._get_request_payload(  # type: ignore[attr-defined, no-any-return]
        [SystemMessage("system"), HumanMessage("hi")],
        **bound.kwargs,  # type: ignore[attr-defined]
    )


def test_anthropic_request_shape(registry: ToolRegistry) -> None:
    settings = Settings(_env_file=None, llm_api_key="sk-test", llm_effort="medium")  # type: ignore[call-arg]
    p = payload(settings, registry)
    assert p["model"] == "claude-opus-5"
    assert p["max_tokens"] == 64000
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"] == {"effort": "medium"}
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["fallbacks"] == "default"
    assert p["betas"] == [ANTHROPIC_FALLBACK_BETA]
    assert [t["name"] for t in p["tools"]] == registry.names
    assert p["tools"][1]["input_schema"]["properties"]["max_chars"]["maximum"] == 200000


def test_anthropic_options_can_be_disabled(registry: ToolRegistry) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        llm_api_key="sk-test",
        llm_model="claude-haiku-4-5",
        llm_thinking=False,
        llm_refusal_fallback=False,
    )
    p = payload(settings, registry)
    assert "thinking" not in p and "fallbacks" not in p and "betas" not in p
    assert p["model"] == "claude-haiku-4-5"


def test_missing_key_is_a_config_error() -> None:
    with pytest.raises(ProviderConfigError):
        build_chat_model(Settings(_env_file=None, llm_api_key=None))  # type: ignore[call-arg]


def test_openai_provider_builds(registry: ToolRegistry) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="openai", llm_api_key="sk-test", llm_model="gpt-5"
    )
    assert build_chat_model(settings).bind_tools(registry.as_langchain_tools()) is not None


def test_google_request_shape(registry: ToolRegistry) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="google", llm_api_key="test-key", llm_effort="max"
    )
    assert settings.model_name == "gemini-2.5-flash"
    model = build_chat_model(settings)
    assert model.include_thoughts is True  # type: ignore[attr-defined]
    assert model.reasoning_effort == "high"  # type: ignore[attr-defined]


def test_ollama_request_shape() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, llm_provider="ollama", llm_context_window=16384
    )
    assert settings.model_name == "qwen3.5:4b"
    model = build_chat_model(settings)
    assert model.num_ctx == 16384  # type: ignore[attr-defined]
    assert model.base_url == "http://127.0.0.1:11434"  # type: ignore[attr-defined]
    assert model.reasoning is True  # type: ignore[attr-defined]
    assert model.keep_alive == "30m"  # type: ignore[attr-defined]
