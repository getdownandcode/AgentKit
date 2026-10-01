"""Application configuration loaded from environment variables using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL.",
    )

    # LLM Settings
    LLM_PROVIDER: Literal["gemini", "openai", "fake"] = Field(
        default="gemini",
        description="LLM provider: gemini, openai, or fake.",
    )
    LLM_MODEL: str = Field(
        default="gemini-3.7-flash",
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
    FILE_TOOL_BASE_DIR: str = Field(
        default="/tmp/agentkit_sandbox",
        description="Base directory for sandboxed file reading operations.",
    )

    # Security & API Authentication
    API_KEYS: str = Field(
        default="ak_test_key_12345",
        description="Comma-separated list of valid client API keys.",
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
    MAX_RETRIES: int = Field(
        default=3,
        ge=0,
        description="Maximum retries on transient LLM errors.",
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
    TOOL_OUTPUT_MAX_CHARS: int = Field(
        default=2000,
        gt=0,
        description="Maximum characters allowed in tool outputs before truncation.",
    )

    @field_validator("API_KEYS")
    @classmethod
    def validate_api_keys(cls, v: str) -> str:
        """Ensure API keys are not empty."""
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("API_KEYS must not be empty")
        return cleaned

    @property
    def api_keys_list(self) -> list[str]:
        """Return parsed and stripped list of valid API keys."""
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton instance of application Settings."""
    return Settings()
