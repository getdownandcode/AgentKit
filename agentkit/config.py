"""Application configuration loaded from environment variables using Pydantic Settings."""

from functools import lru_cache
from typing import Literal
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def apply_redis_password(url: str, password: str | None) -> str:
    """Return ``url`` with ``password`` embedded when it carries no credentials.

    The hardened compose stack requires ``REDIS_PASSWORD`` and starts Redis with
    ``--requirepass``, but keeping the secret out of ``REDIS_URL`` is the safer way to
    configure a deployment (a URL ends up in logs, process listings and crash dumps).
    An explicit credential in the URL always wins, so pointing at a passwordless Redis
    for local work still behaves exactly as before.
    """
    if not password:
        return url

    parts: SplitResult = urlsplit(url)
    if parts.scheme not in {"redis", "rediss"}:
        return url
    # An existing userinfo (``redis://user@host``) or password must never be overridden.
    if "@" in parts.netloc or parts.password:
        return url
    if not parts.netloc:
        return url

    userinfo = f":{quote(password, safe='')}"
    return urlunsplit(parts._replace(netloc=f"{userinfo}@{parts.netloc}"))


class Settings(BaseSettings):
    """Global configuration settings for AgentKit."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database and Cache
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit",
        description="Async PostgreSQL connection URL.",
    )
    DATABASE_READONLY_URL: str | None = Field(
        default=None,
        description=(
            "Async PostgreSQL URL for the dedicated non-superuser role used by the "
            "sql_readonly tool. The application keeps using DATABASE_URL; the tool refuses "
            "to run without this rather than falling back to the application role."
        ),
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )
    REDIS_PASSWORD: str | None = Field(
        default=None,
        description=(
            "Password for Redis. Completed into REDIS_URL when the URL itself carries no "
            "credentials, so the secret can stay out of the URL string."
        ),
    )

    # LLM Settings
    LLM_PROVIDER: Literal["gemini", "openai", "fake"] = Field(
        default="gemini",
        description="LLM provider: gemini, openai, or fake.",
    )
    LLM_MODEL: str = Field(
        default="gemini-3.1-flash-lite",
        description="LLM model identifier.",
    )
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Google Gemini API key.",
    )
    OPENAI_API_KEY: str | None = Field(
        default=None,
        description="OpenAI API key.",
    )

    # External Tools & Sandboxing
    SEARCH_API_KEY: str | None = Field(
        default=None,
        description="External search API key (Tavily/SerpAPI).",
    )
    LLM_MAX_RETRIES: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Retry attempts for transient LLM provider failures (429/5xx/network).",
    )
    LLM_RETRY_BASE_DELAY_S: float = Field(
        default=0.5,
        gt=0,
        description="Base delay in seconds for exponential LLM retry backoff.",
    )
    LLM_RETRY_MAX_DELAY_S: float = Field(
        default=10.0,
        gt=0,
        description="Maximum delay in seconds between LLM retry attempts.",
    )
    FILE_TOOL_BASE_DIR: str = Field(
        default="/tmp/agentkit_sandbox",
        description="Base directory for sandboxed file reading operations.",
    )

    # Security & API Authentication
    API_KEYS: str = Field(
        default="",
        description="Comma-separated list of valid client API keys. Must be set explicitly.",
    )
    RATE_LIMIT_PER_MIN: int = Field(
        default=30,
        gt=0,
        description="Allowed requests per minute per API key.",
    )

    # Agent Runtime Limits
    MAX_STEPS: int = Field(
        default=10,
        gt=0,
        description="Maximum execution steps allowed per agent run.",
    )
    RUN_TIMEOUT_S: int = Field(
        default=60,
        gt=0,
        description="Maximum overall execution time in seconds for a run.",
    )
    TOOL_TIMEOUT_S: int = Field(
        default=15,
        gt=0,
        description="Maximum execution time in seconds per tool execution.",
    )
    SESSION_TTL_S: int = Field(
        default=3600,
        gt=0,
        description="TTL in seconds for Redis conversation session cache.",
    )
    SESSION_MAX_MESSAGES: int = Field(
        default=40,
        gt=0,
        description=(
            "Maximum messages retained per session history (system prompt included). "
            "Truncation never leaves a tool result without its tool call."
        ),
    )
    TOOL_OUTPUT_MAX_CHARS: int = Field(
        default=2000,
        gt=0,
        description="Maximum characters allowed in tool outputs before truncation.",
    )

    @model_validator(mode="after")
    def _complete_redis_url(self) -> "Settings":
        """Embed ``REDIS_PASSWORD`` into a credential-less ``REDIS_URL``.

        Keeps a single source of truth for the resolved URL so the API and the
        real-infrastructure test suite authenticate against the same hardened Redis.
        """
        self.REDIS_URL = apply_redis_password(self.REDIS_URL, self.REDIS_PASSWORD)
        return self

    @field_validator("API_KEYS")
    @classmethod
    def validate_api_keys(cls, v: str) -> str:
        """Reject blank or placeholder keys so a deployment cannot start unauthenticated.

        The default is intentionally empty: starting with a key published in the repository
        would expose any deployment that forgets to configure its own.
        """
        cleaned = v.strip()
        if not cleaned:
            raise ValueError(
                "API_KEYS must be set to a comma-separated list of client API keys. "
                "AgentKit refuses to start without explicit authentication credentials."
            )
        return cleaned

    @property
    def api_keys_list(self) -> list[str]:
        """Return parsed and stripped list of valid API keys."""
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of application Settings."""
    return Settings()
