"""Builds the configured LangChain chat model."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.llm.provider import LangChainProvider

# Anthropic beta that enables `fallbacks: "default"` (server-side re-serving of
# policy-declined requests by a fallback model within the same call).
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ProviderConfigError(ValueError):
    pass


def build_chat_model(settings: Settings) -> BaseChatModel:
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None

    match settings.llm_provider:
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            if not api_key:
                raise ProviderConfigError("LLM_API_KEY is required for the anthropic provider")
            kwargs: dict[str, Any] = {
                "model": settings.model_name,
                "api_key": api_key,
                # Streaming requests can use a generous output cap safely.
                "max_tokens": settings.llm_max_tokens or 64000,
                "max_retries": 2,
                # Automatic prompt caching: the API places a breakpoint on the
                # newest block, so each agent step reuses the cached prefix.
                "model_kwargs": {"cache_control": {"type": "ephemeral"}},
            }
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            if settings.llm_thinking:
                kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            if settings.llm_effort:
                kwargs["reasoning_effort"] = settings.llm_effort
            if settings.llm_refusal_fallback:
                kwargs["betas"] = [ANTHROPIC_FALLBACK_BETA]
                kwargs["model_kwargs"]["fallbacks"] = "default"
            return ChatAnthropic(**kwargs)

        case "google":
            from langchain_google_genai import ChatGoogleGenerativeAI

            if not api_key:
                raise ProviderConfigError("LLM_API_KEY is required for the google provider")
            kwargs = {"model": settings.model_name, "google_api_key": api_key, "max_retries": 2}
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            if settings.llm_max_tokens:
                kwargs["max_output_tokens"] = settings.llm_max_tokens
            if settings.llm_thinking:
                kwargs["include_thoughts"] = True
            if settings.llm_effort:
                # Gemini's levels stop at "high".
                effort = settings.llm_effort
                kwargs["reasoning_effort"] = "high" if effort in ("xhigh", "max") else effort
            return ChatGoogleGenerativeAI(**kwargs)

        case "openai":
            from langchain_openai import ChatOpenAI

            if not api_key:
                raise ProviderConfigError("LLM_API_KEY is required for the openai provider")
            kwargs = {"model": settings.model_name, "api_key": api_key, "max_retries": 2}
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            if settings.llm_max_tokens:
                kwargs["max_tokens"] = settings.llm_max_tokens
            if settings.llm_effort:
                kwargs["reasoning_effort"] = settings.llm_effort
            return ChatOpenAI(**kwargs)

        case "ollama":
            from langchain_ollama import ChatOllama

            kwargs = {
                "model": settings.model_name,
                "base_url": settings.llm_base_url or settings.ollama_base_url,
                "num_ctx": settings.llm_context_window,
                # Keep the model loaded between agent steps.
                "keep_alive": "30m",
                # Separate thinking from the answer (Qwen 3.x thinking models).
                "reasoning": settings.llm_thinking,
            }
            if settings.llm_max_tokens:
                kwargs["num_predict"] = settings.llm_max_tokens
            return ChatOllama(**kwargs)

    raise ProviderConfigError(f"unsupported LLM_PROVIDER {settings.llm_provider!r}")


# Schema keywords a provider does not accept in tool definitions.
_UNSUPPORTED_SCHEMA_KEYS: dict[str, frozenset[str]] = {
    "google": frozenset({"additionalProperties"}),
}


def build_provider(settings: Settings) -> LangChainProvider:
    return LangChainProvider(
        settings.llm_provider,
        build_chat_model(settings),
        drop_schema_keys=_UNSUPPORTED_SCHEMA_KEYS.get(settings.llm_provider, frozenset()),
    )
