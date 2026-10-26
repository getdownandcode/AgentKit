"""Regression tests for loop detection and API key comparison (AUDIT-15 / AUDIT-17)."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

import pytest

from agentkit.api.auth import verify_api_key
from agentkit.config import Settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


def _calculator_registry() -> ToolRegistry:
    registry = ToolRegistry()

    @tool(registry=registry)
    def calculator(x: int) -> int:
        """Echo a number."""
        return x

    return registry


def _tool_response(call_id: str, x: int) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name="calculator", arguments={"x": x})],
    )


# --- AUDIT-15: sliding-window duplicate detection --------------------------------------------


@pytest.mark.asyncio
async def test_alternating_identical_calls_are_detected_as_a_loop() -> None:
    """A, B, A, B used to reset the counter on every step and never fire."""
    llm = FakeLLMClient(
        responses=[
            _tool_response("c1", 1),
            _tool_response("c2", 2),
            _tool_response("c3", 1),
            _tool_response("c4", 2),
            _tool_response("c5", 1),
            LLMResponse(text="unreachable"),
        ]
    )
    agent = Agent(
        llm=llm,
        registry=_calculator_registry(),
        config=AgentConfig(
            max_steps=10,
            max_consecutive_duplicate_tool_calls=3,
            duplicate_tool_call_window=20,
        ),
    )

    result = await agent.run(goal="alternate forever")

    assert result.status == RunStatus.FAILED
    assert result.failure_reason is not None
    assert "duplicate tool call loop" in result.failure_reason.lower()
    assert result.steps_count < 10, "the loop guard must fire before max_steps"


@pytest.mark.asyncio
async def test_consecutive_repeats_still_abort_the_run() -> None:
    """The original behaviour must not regress."""
    llm = FakeLLMClient(
        responses=[
            _tool_response("c1", 7),
            _tool_response("c2", 7),
            _tool_response("c3", 7),
            LLMResponse(text="unreachable"),
        ]
    )
    agent = Agent(
        llm=llm,
        registry=_calculator_registry(),
        config=AgentConfig(max_steps=10, max_consecutive_duplicate_tool_calls=3),
    )

    result = await agent.run(goal="repeat")

    assert result.status == RunStatus.FAILED
    assert "duplicate tool call loop" in (result.failure_reason or "").lower()


@pytest.mark.asyncio
async def test_varied_calls_are_not_flagged() -> None:
    """Detecting repeats must not turn every multi-step run into a loop failure."""
    llm = FakeLLMClient(
        responses=[
            _tool_response("c1", 1),
            _tool_response("c2", 2),
            _tool_response("c3", 3),
            LLMResponse(text="finished"),
        ]
    )
    agent = Agent(
        llm=llm,
        registry=_calculator_registry(),
        config=AgentConfig(max_steps=10, max_consecutive_duplicate_tool_calls=2),
    )

    result = await agent.run(goal="do three things")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "finished"


@pytest.mark.asyncio
async def test_window_size_bounds_what_the_detector_remembers() -> None:
    """A narrow window only sees recent calls, which is what makes the scope explicit."""
    llm = FakeLLMClient(
        responses=[
            _tool_response("c1", 1),
            _tool_response("c2", 2),
            _tool_response("c3", 1),
            _tool_response("c4", 2),
            LLMResponse(text="finished"),
        ]
    )
    agent = Agent(
        llm=llm,
        registry=_calculator_registry(),
        config=AgentConfig(
            max_steps=10,
            max_consecutive_duplicate_tool_calls=2,
            duplicate_tool_call_window=2,
        ),
    )

    result = await agent.run(goal="alternate")

    assert result.status == RunStatus.SUCCEEDED, "with a 2-call window, repeats fall out"


# --- AUDIT-17: constant-time API key comparison ----------------------------------------------


def _settings() -> Settings:
    return Settings(API_KEYS="first_key,second_key")


@pytest.mark.asyncio
async def test_surrounding_whitespace_is_normalised() -> None:
    """A padded key must behave exactly like an unpadded one."""
    monkey_settings = _settings()

    assert await verify_api_key(api_key="first_key", settings=monkey_settings) == "first_key"
    assert await verify_api_key(api_key=" first_key ", settings=monkey_settings) == "first_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("presented", [None, "", "   ", "wrong_key", "first_ke"])
async def test_invalid_keys_are_rejected(presented: str | None) -> None:
    """Missing, blank and near-miss keys are all unauthorized."""
    from agentkit.core.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        await verify_api_key(api_key=presented, settings=_settings())


@pytest.mark.asyncio
async def test_comparison_covers_every_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short-circuiting on the first match would leak which key matched through timing."""
    calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def _spy(left: Any, right: Any) -> bool:
        calls.append((left, right))
        return bool(real_compare(left, right))

    monkeypatch.setattr(hmac, "compare_digest", _spy)
    settings = Settings(API_KEYS="match_key,other_key,third_key")

    resolved = await verify_api_key(api_key="match_key", settings=settings)

    assert resolved == "match_key"
    assert len(calls) == len(settings.api_keys_list), "every configured key must be compared"


@pytest.mark.asyncio
async def test_comparison_runs_through_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain ``in`` check is a timing oracle; the digest comparison must be used."""
    used: list[str] = []
    real_compare: Callable[[bytes, bytes], bool] = hmac.compare_digest

    def _spy(left: Any, right: Any) -> bool:
        used.append("compare_digest")
        return bool(real_compare(left, right))

    monkeypatch.setattr(hmac, "compare_digest", _spy)

    await verify_api_key(api_key="first_key", settings=_settings())

    assert used, "hmac.compare_digest was never called"
