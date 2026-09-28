"""Unit tests for configuration management via Pydantic BaseSettings."""

import pytest
from pydantic import ValidationError

from agentkit.config import Settings


def test_default_settings() -> None:
    """Verify default configuration values."""
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit",
        REDIS_URL="redis://localhost:6379/0",
        API_KEYS="key1,key2",
    )
    assert settings.LLM_PROVIDER == "gemini"
    assert settings.LLM_MODEL == "gemini-2.5-flash"
    assert settings.MAX_STEPS == 10
    assert settings.RUN_TIMEOUT_S == 60
    assert settings.TOOL_TIMEOUT_S == 15
    assert settings.RATE_LIMIT_PER_MIN == 30
    assert settings.SESSION_TTL_S == 3600
    assert settings.TOOL_OUTPUT_MAX_CHARS == 2000
    assert settings.api_keys_list == ["key1", "key2"]


def test_custom_settings_and_api_keys_parsing() -> None:
    """Verify comma-separated API keys are stripped and parsed properly."""
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
        REDIS_URL="redis://localhost:6379/1",
        API_KEYS="  key_a ,  key_b , key_c  ",
        LLM_PROVIDER="openai",
        LLM_MODEL="gpt-4o-mini",
        MAX_STEPS=25,
    )
    assert settings.LLM_PROVIDER == "openai"
    assert settings.LLM_MODEL == "gpt-4o-mini"
    assert settings.MAX_STEPS == 25
    assert settings.api_keys_list == ["key_a", "key_b", "key_c"]


def test_invalid_provider_raises_validation_error() -> None:
    """Verify invalid provider raises ValidationError."""
    with pytest.raises(ValidationError):
        Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
            REDIS_URL="redis://localhost:6379/1",
            API_KEYS="key",
            LLM_PROVIDER="unsupported_provider",  # type: ignore[arg-type]
        )


def test_non_positive_steps_raises_validation_error() -> None:
    """Verify non-positive MAX_STEPS raises ValidationError."""
    with pytest.raises(ValidationError):
        Settings(
            DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db",
            REDIS_URL="redis://localhost:6379/1",
            API_KEYS="key",
            MAX_STEPS=0,
        )
