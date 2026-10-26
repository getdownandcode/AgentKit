"""Regression tests for provider refusals, blocks and empty completions (AUDIT-07).

None of these outcomes is an SDK exception: a blocked or filtered response is a normal 200
whose content is empty. Without explicit detection they were reported as successful runs with
a blank ``final_answer``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from agentkit.api.deps import get_agent, get_memory_store
from agentkit.api.main import create_app
from agentkit.config import Settings
from agentkit.core.agent import Agent
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse, Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.llm.gemini import GeminiClient
from agentkit.llm.openai import OpenAIClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.tools.registry import ToolRegistry


def _gemini_response(
    parts: list[types.Part],
    *,
    block_reason: Any = None,
    finish_reason: Any = None,
    text: str = "",
) -> Any:
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=parts) if parts else None,
        finish_reason=finish_reason,
    )
    return SimpleNamespace(
        candidates=[candidate],
        text=text,
        prompt_feedback=SimpleNamespace(block_reason=block_reason),
        usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=0),
        model_dump=lambda: {},
    )


def _gemini_client(response: Any) -> GeminiClient:
    sdk = MagicMock()
    sdk.aio.models.generate_content = AsyncMock(return_value=response)
    return GeminiClient(api_key="fake-key", client=sdk)


def _openai_client(response: Any) -> OpenAIClient:
    sdk = MagicMock()
    sdk.chat.completions.create = AsyncMock(return_value=response)
    return OpenAIClient(api_key="fake-key", client=sdk)


def _openai_response(
    *,
    content: str | None = None,
    finish_reason: str | None = "stop",
    refusal: str | None = None,
) -> Any:
    message = SimpleNamespace(content=content, tool_calls=None, refusal=refusal)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=0),
        model_dump=lambda: {},
    )


@pytest.mark.asyncio
async def test_gemini_blocked_prompt_is_a_refusal_not_an_empty_answer() -> None:
    """prompt_feedback.block_reason used to be invisible to the adapter."""
    adapter = _gemini_client(_gemini_response([], block_reason=types.BlockedReason.SAFETY, text=""))

    response = await adapter.chat([Message.user("something disallowed")])

    assert response.text == ""
    assert response.tool_calls == []
    assert response.refusal_reason is not None
    assert "SAFETY" in response.refusal_reason


@pytest.mark.asyncio
async def test_gemini_blocked_finish_reason_is_a_refusal() -> None:
    """A generation stopped by the safety filter must not read as a completed answer."""
    adapter = _gemini_client(
        _gemini_response(
            [types.Part(text="partial")],
            finish_reason=types.FinishReason.SAFETY,
            text="partial",
        )
    )

    response = await adapter.chat([Message.user("question")])

    assert response.refusal_reason is not None
    assert "SAFETY" in response.refusal_reason


@pytest.mark.asyncio
async def test_gemini_normal_completion_has_no_refusal() -> None:
    """The detection must not flag an ordinary STOP."""
    adapter = _gemini_client(
        _gemini_response(
            [types.Part(text="42")],
            finish_reason=types.FinishReason.STOP,
            text="42",
        )
    )

    response = await adapter.chat([Message.user("question")])

    assert response.refusal_reason is None
    assert response.text == "42"


@pytest.mark.asyncio
async def test_openai_content_filter_is_a_refusal() -> None:
    """finish_reason == 'content_filter' produces a normal, empty completion."""
    adapter = _openai_client(_openai_response(content=None, finish_reason="content_filter"))

    response = await adapter.chat([Message.user("question")])

    assert response.refusal_reason is not None
    assert "content_filter" in response.refusal_reason


@pytest.mark.asyncio
async def test_openai_explicit_refusal_is_surfaced() -> None:
    """A structured refusal carries the provider's own wording."""
    adapter = _openai_client(
        _openai_response(content=None, refusal="I cannot help with that request.")
    )

    response = await adapter.chat([Message.user("question")])

    assert response.refusal_reason is not None
    assert "I cannot help with that request." in response.refusal_reason


@pytest.mark.asyncio
async def test_openai_normal_completion_has_no_refusal() -> None:
    """An ordinary answer is unaffected."""
    adapter = _openai_client(_openai_response(content="The answer.", finish_reason="stop"))

    response = await adapter.chat([Message.user("question")])

    assert response.refusal_reason is None
    assert response.text == "The answer."


@pytest.mark.asyncio
async def test_agent_reports_refused_status_with_the_reason() -> None:
    """A refusal becomes RunStatus.REFUSED, never SUCCEEDED with an empty answer."""
    agent = Agent(
        llm=FakeLLMClient(
            responses=[LLMResponse(text="", refusal_reason="Gemini blocked the prompt (SAFETY).")]
        ),
        registry=ToolRegistry(),
    )

    result = await agent.run(goal="something disallowed")

    assert result.status == RunStatus.REFUSED
    assert result.final_answer is None
    assert result.failure_reason is not None
    assert "SAFETY" in result.failure_reason


@pytest.mark.asyncio
async def test_agent_fails_on_an_empty_provider_response() -> None:
    """No text and no tool calls is a failure, not an empty success."""
    agent = Agent(
        llm=FakeLLMClient(responses=[LLMResponse(text="")]),
        registry=ToolRegistry(),
    )

    result = await agent.run(goal="anything")

    assert result.status == RunStatus.FAILED
    assert result.final_answer is None
    assert result.failure_reason is not None
    assert "empty response" in result.failure_reason.lower()


@pytest.mark.asyncio
async def test_successful_runs_always_carry_a_non_empty_answer() -> None:
    """The success path keeps working unchanged for a real answer."""
    agent = Agent(
        llm=FakeLLMClient(responses=[LLMResponse(text="The answer is 42.")]),
        registry=ToolRegistry(),
    )

    result = await agent.run(goal="what is the answer?")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The answer is 42."


def test_api_returns_the_refused_status_and_reason() -> None:
    """POST /runs must expose the refusal instead of a blank 200 success."""
    settings = Settings(API_KEYS="secret_token")
    app = create_app(settings=settings)
    store = InMemoryMemoryStore()
    agent = Agent(
        llm=FakeLLMClient(
            responses=[LLMResponse(text="", refusal_reason="OpenAI filtered the response.")]
        ),
        registry=ToolRegistry(),
        memory=store,
    )
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_memory_store] = lambda: store

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/runs",
        json={"goal": "question"},
        headers={"X-API-Key": "secret_token"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == RunStatus.REFUSED.value
    assert body["answer"] is None
    assert body["failure_reason"] == "OpenAI filtered the response."
