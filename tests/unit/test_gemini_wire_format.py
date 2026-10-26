"""Regression tests for the Gemini adapter wire format (AUDIT-05 / AUDIT-06)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import types

from agentkit.llm.base import Message
from agentkit.llm.gemini import GeminiClient
from agentkit.tools.models import ToolCall


def _client() -> tuple[GeminiClient, MagicMock]:
    sdk = MagicMock()
    sdk.aio.models.generate_content = AsyncMock()
    return GeminiClient(api_key="fake-key", client=sdk), sdk


def _response(parts: list[types.Part], text: str = "") -> Any:
    """Minimal stand-in for a GenerateContentResponse built from real SDK Parts."""
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts))
    return SimpleNamespace(
        candidates=[candidate],
        text=text,
        usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=5),
        model_dump=lambda: {},
    )


def _sent_contents(sdk: MagicMock) -> list[types.Content]:
    return list(sdk.aio.models.generate_content.call_args.kwargs["contents"])


def _function_responses(content: types.Content) -> list[types.FunctionResponse]:
    """Return the function responses in a content block, ignoring other parts."""
    return [p.function_response for p in content.parts or [] if p.function_response is not None]


def _function_calls(content: types.Content) -> list[types.FunctionCall]:
    """Return the function calls in a content block, ignoring other parts."""
    return [p.function_call for p in content.parts or [] if p.function_call is not None]


@pytest.mark.asyncio
async def test_parallel_calls_preserve_the_provider_ids() -> None:
    """Discarding FunctionCall.id breaks correlation between two calls to one tool."""
    adapter, sdk = _client()
    sdk.aio.models.generate_content.return_value = _response(
        [
            types.Part(
                function_call=types.FunctionCall(id="fc1", name="calculator", args={"x": 1})
            ),
            types.Part(
                function_call=types.FunctionCall(id="fc2", name="calculator", args={"x": 2})
            ),
        ]
    )

    response = await adapter.chat([Message.user("add 1 and 2")])

    assert [call.id for call in response.tool_calls] == ["fc1", "fc2"]
    assert [call.arguments for call in response.tool_calls] == [{"x": 1}, {"x": 2}]


@pytest.mark.asyncio
async def test_parallel_tool_results_are_sent_in_one_turn() -> None:
    """One Content per tool result makes Gemini reject the whole turn with 400."""
    adapter, sdk = _client()
    sdk.aio.models.generate_content.return_value = _response([types.Part(text="done")])

    messages = [
        Message.user("add 1 and 2"),
        Message.assistant(
            "",
            [
                ToolCall(id="fc1", name="calculator", arguments={"x": 1}),
                ToolCall(id="fc2", name="calculator", arguments={"x": 2}),
            ],
        ),
        Message.tool_result("fc1", "1"),
        Message.tool_result("fc2", "2"),
    ]

    await adapter.chat(messages)

    contents = _sent_contents(sdk)
    tool_turns = [c for c in contents if _function_responses(c)]
    assert len(tool_turns) == 1, "all results for one assistant turn belong to one Content"

    responses = _function_responses(tool_turns[0])
    assert len(responses) == 2
    assert [r.id for r in responses] == ["fc1", "fc2"]
    assert [r.name for r in responses] == ["calculator", "calculator"]
    assert [r.response for r in responses] == [{"result": "1"}, {"result": "2"}]

    model_turn = next(c for c in contents if c.role == "model")
    assert [call.id for call in _function_calls(model_turn)] == ["fc1", "fc2"]


@pytest.mark.asyncio
async def test_single_tool_call_still_emits_one_function_response() -> None:
    """The grouping change must not alter the single-call shape."""
    adapter, sdk = _client()
    sdk.aio.models.generate_content.return_value = _response([types.Part(text="done")])

    await adapter.chat(
        [
            Message.assistant("", [ToolCall(id="fc9", name="calculator", arguments={})]),
            Message.tool_result("fc9", "9"),
        ]
    )

    tool_turns = [c for c in _sent_contents(sdk) if _function_responses(c)]
    assert len(tool_turns) == 1
    responses = _function_responses(tool_turns[0])
    assert responses[0].id == "fc9"
    assert responses[0].response == {"result": "9"}


@pytest.mark.asyncio
async def test_thought_parts_are_excluded_from_the_answer() -> None:
    """Internal reasoning must not become the answer, the history or the API response."""
    adapter, sdk = _client()
    sdk.aio.models.generate_content.return_value = _response(
        [
            types.Part(text="SECRET CHAIN OF THOUGHT", thought=True),
            types.Part(text="The answer is 42."),
        ],
        text="SECRET CHAIN OF THOUGHTThe answer is 42.",
    )

    response = await adapter.chat([Message.user("what is the answer?")])

    assert response.text == "The answer is 42."
    assert "SECRET" not in response.text


@pytest.mark.asyncio
async def test_thought_only_response_does_not_leak_text() -> None:
    """A response that is entirely reasoning must not fall back to raw_response.text."""
    adapter, sdk = _client()
    sdk.aio.models.generate_content.return_value = _response(
        [types.Part(text="SECRET CHAIN OF THOUGHT", thought=True)],
        text="SECRET CHAIN OF THOUGHT",
    )

    response = await adapter.chat([Message.user("hi")])

    assert response.text == ""
    assert response.tool_calls == []


@pytest.mark.asyncio
async def test_thought_part_carrying_a_function_call_still_yields_the_call() -> None:
    """The call and its signature are not lost just because the part is a thought."""
    adapter, sdk = _client()
    sdk.aio.models.generate_content.return_value = _response(
        [
            types.Part(
                function_call=types.FunctionCall(id="fc7", name="calculator", args={"x": 7}),
                thought=True,
                thought_signature=b"\xff\x00sig",
            )
        ]
    )

    response = await adapter.chat([Message.user("compute")])

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id == "fc7"
    assert call.name == "calculator"
    assert call.thought_signature == b"\xff\x00sig"
    assert response.text == ""
