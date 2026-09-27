"""Application configuration, loaded from environment variables (and .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"

LLMProviderName = Literal["anthropic", "google", "openai", "ollama"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
Environment = Literal["development", "production"]
ToolRisk = Literal["read", "interact", "high_impact"]

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-5",
    "google": "gemini-2.5-flash",
    "openai": "gpt-5",
    "ollama": "qwen3.5:4b",
}

LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # production turns on the checks in _check_production.
    environment: Environment = "development"

    # HTTP server. Loopback by default. To serve other machines, bind to
    # 0.0.0.0 behind a TLS-terminating proxy and list the public host name
    # in ALLOWED_HOSTS.
    host: str = "127.0.0.1"
    port: int = 8787
    log_level: str = "INFO"
    log_json: bool = False
    # Host header values accepted (blocks DNS rebinding).
    allowed_hosts: list[str] = Field(default_factory=lambda: list(LOOPBACK_HOSTS))
    # Proxies whose X-Forwarded-For is trusted for client addresses.
    forwarded_allow_ips: str = "127.0.0.1"

    # Chrome extension IDs allowed to call the API. Empty means any
    # chrome-extension:// origin is accepted, which is only suitable for
    # local development with an unpacked extension.
    allowed_extension_ids: list[str] = Field(default_factory=list)

    # Storage. DATABASE_URL empty = a SQLite file in DATA_DIR (development).
    data_dir: Path = BACKEND_ROOT / "data"
    database_url: str = ""
    database_auto_migrate: bool = True
    # Redis for sign-in sessions, task locks and rate limits. Empty = kept
    # in this process (single instance; sign-ins end on restart).
    redis_url: str | None = None
    # Comma-separated Fernet keys; the first encrypts (see app/db/crypto.py).
    # Empty in development = a key generated into DATA_DIR/encryption.key.
    data_encryption_keys: SecretStr | None = None
    # Delete tasks older than this many days (0 keeps them).
    task_retention_days: int = Field(default=0, ge=0)

    # Accounts. The first account can always be created; after that only
    # when registration is open.
    allow_registration: bool = False
    session_ttl_hours: int = Field(default=720, ge=1)

    # Rate limits (per user, or per client address before sign-in).
    rate_limit_logins_per_15m: int = Field(default=10, ge=1)
    rate_limit_tasks_per_minute: int = Field(default=20, ge=1)
    rate_limit_requests_per_minute: int = Field(default=300, ge=1)
    max_running_tasks_per_user: int = Field(default=3, ge=1)

    # Sites the agent may use. ALLOWED_DOMAINS empty = any site except
    # BLOCKED_DOMAINS. A domain covers its subdomains.
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)

    # Tool risk levels that always need the user's approval before running.
    # Clicks the extension recognizes as high impact (buying, sending,
    # deleting...) need it regardless.
    confirm_risk_levels: list[ToolRisk] = Field(default_factory=lambda: ["high_impact"])  # type: ignore[arg-type]
    confirmation_timeout_seconds: float = Field(default=300.0, gt=0)

    # LLM.
    llm_provider: LLMProviderName = "anthropic"
    llm_api_key: SecretStr | None = None
    # Empty means the provider's default (see DEFAULT_MODELS).
    llm_model: str = ""
    llm_base_url: str | None = None
    llm_max_tokens: int | None = None
    llm_effort: Effort | None = None
    # Anthropic, Google: stream reasoning summaries.
    llm_thinking: bool = True
    # Anthropic only: server-side refusal fallbacks.
    llm_refusal_fallback: bool = True
    # Ollama: server address and context window. Ollama's own default window
    # is small and it silently drops the start of longer prompts, so the
    # backend always sets one.
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_context_window: int = Field(default=32768, ge=4096)

    # Agent.
    agent_max_steps: int = Field(default=25, ge=1, le=200)
    tool_timeout_seconds: float = Field(default=30.0, gt=0)
    # How long the agent waits for the user to answer a question.
    user_reply_timeout_seconds: float = Field(default=900.0, gt=0)
    # Upper bounds on what page tools return, to fit smaller context windows.
    agent_page_text_limit: int = Field(default=40000, ge=1000, le=200000)
    agent_element_limit: int = Field(default=150, ge=10, le=400)
    shared_tools_path: Path = REPO_ROOT / "shared" / "tools.json"

    @property
    def model_name(self) -> str:
        return self.llm_model or DEFAULT_MODELS[self.llm_provider]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.data_dir / 'browser_agent.db'}"

    @model_validator(mode="after")
    def _check_production(self) -> Self:
        if not self.is_production:
            return self
        problems = []
        if not self.database_url.startswith("postgresql"):
            problems.append("DATABASE_URL must point to PostgreSQL")
        if not self.redis_url:
            problems.append("REDIS_URL is required")
        if self.data_encryption_keys is None:
            problems.append("DATA_ENCRYPTION_KEYS is required")
        if not self.allowed_extension_ids:
            problems.append("ALLOWED_EXTENSION_IDS must list your extension's ID")
        if problems:
            raise ValueError("ENVIRONMENT=production: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
