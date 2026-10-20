"""Unit tests for configuration management via Pydantic BaseSettings."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from agentkit.api.deps import get_agent
from agentkit.config import Settings
from agentkit.core.trace import InMemoryTraceSink
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


def test_default_settings() -> None:
    """Verify default configuration values."""
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit",
        REDIS_URL="redis://localhost:6379/0",
        API_KEYS="key1,key2",
    )
    assert settings.LLM_PROVIDER == "gemini"
    assert settings.LLM_MODEL == "gemini-3.1-flash-lite"
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


def test_get_agent_wires_tool_output_max_chars() -> None:
    """AUDIT-12: get_agent wires Settings.TOOL_OUTPUT_MAX_CHARS to AgentConfig."""
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit",
        REDIS_URL="redis://localhost:6379/0",
        API_KEYS="secret_key",
        TOOL_OUTPUT_MAX_CHARS=50,
    )

    class _DummyApp:
        state = SimpleNamespace(agent_override=None)

    request = SimpleNamespace(app=_DummyApp())

    agent = get_agent(
        request=request,  # type: ignore[arg-type]
        settings=settings,
        llm=FakeLLMClient(),
        registry=ToolRegistry(),
        memory=InMemoryMemoryStore(),
        trace=InMemoryTraceSink(),
    )
    assert agent.config.tool_output_max_chars == 50


@pytest.mark.asyncio
async def test_tool_output_truncation_with_configured_max_chars() -> None:
    """AUDIT-12: Tool outputs exceeding configured max_chars are truncated."""
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit",
        REDIS_URL="redis://localhost:6379/0",
        API_KEYS="secret_key",
        TOOL_OUTPUT_MAX_CHARS=40,
    )

    registry = ToolRegistry()

    def long_output_tool() -> str:
        return "A" * 120

    registry.register(long_output_tool, name="long_tool", description="Returns long string.")

    fake_llm = FakeLLMClient(
        responses=[
            LLMResponse(
                tool_calls=[ToolCall(id="call_long", name="long_tool", arguments={})],
            ),
            LLMResponse(text="Done"),
        ]
    )

    class _DummyApp:
        state = SimpleNamespace(agent_override=None)

    request = SimpleNamespace(app=_DummyApp())

    agent = get_agent(
        request=request,  # type: ignore[arg-type]
        settings=settings,
        llm=fake_llm,
        registry=registry,
        memory=InMemoryMemoryStore(),
        trace=InMemoryTraceSink(),
    )

    await agent.run("Run long tool")

    # fake_llm.history records list of messages passed on each turn
    all_messages = [msg for turn in fake_llm.history for msg in turn]
    tool_msg = next(m for m in all_messages if m.role.value == "tool")
    assert tool_msg.content.startswith("A" * 40)
    assert "[Output truncated: exceeded 40 character limit]" in tool_msg.content


def test_docker_compose_environment_covers_all_settings() -> None:
    """AUDIT-12: docker-compose api environment declares all settings in Settings."""
    compose_path = Path(__file__).parents[2] / "docker-compose.yml"
    with open(compose_path, encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    api_env = compose_data["services"]["api"]["environment"]
    env_keys: set[str] = set()
    for item in api_env:
        key = item.split("=")[0].strip()
        env_keys.add(key)

    settings_fields = set(Settings.model_fields.keys())
    missing = settings_fields - env_keys
    assert not missing, f"Missing settings in docker-compose.yml: {missing}"


def test_spec_documents_all_settings() -> None:
    """AUDIT-12: docs/SPEC.md and docs/AGENTKIT_SPEC.md document all settings."""
    spec_dir = Path(__file__).parents[2] / "docs"
    for filename in ["SPEC.md", "AGENTKIT_SPEC.md"]:
        path = spec_dir / filename
        content = path.read_text(encoding="utf-8")
        for field in Settings.model_fields:
            assert f"`{field}`" in content, f"Setting {field} missing from {filename}"
