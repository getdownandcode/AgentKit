"""Unit tests for duplicate tool call and infinite loop detection."""

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.errors import DuplicateToolCallLoopError
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


@pytest.mark.asyncio
async def test_duplicate_tool_call_halts_with_failure_status() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def search_query(q: str) -> str:
        """Search query."""
        return f"result for {q}"

    # 3 identical consecutive calls with q="python"
    identical_call = ToolCall(id="c1", name="search_query", arguments={"q": "python"})
    llm = FakeLLMClient(
        responses=[
            LLMResponse(text="Searching...", tool_calls=[identical_call]),
            LLMResponse(text="Searching again...", tool_calls=[identical_call]),
            LLMResponse(text="Searching again 2...", tool_calls=[identical_call]),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=10, max_consecutive_duplicate_tool_calls=3),
    )
    result = await agent.run(goal="Infinite loop")

    assert result.status == RunStatus.FAILED
    assert "duplicate tool call loop" in (result.failure_reason or "").lower()
    assert "search_query" in (result.failure_reason or "")
    assert result.final_answer is None


@pytest.mark.asyncio
async def test_duplicate_tool_call_raises_when_requested() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def repeat_me(val: int) -> int:
        """Repeat."""
        return val

    identical_call = ToolCall(id="c1", name="repeat_me", arguments={"val": 42})
    llm = FakeLLMClient(
        responses=[
            LLMResponse(text="Repeat 1", tool_calls=[identical_call]),
            LLMResponse(text="Repeat 2", tool_calls=[identical_call]),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5, max_consecutive_duplicate_tool_calls=2),
    )

    with pytest.raises(DuplicateToolCallLoopError) as exc_info:
        await agent.run(goal="Test raise", raise_on_failure=True)

    assert exc_info.value.tool_name == "repeat_me"
    assert exc_info.value.count == 2
    assert exc_info.value.code == "DUPLICATE_TOOL_CALL_LOOP"


@pytest.mark.asyncio
async def test_different_arguments_do_not_trigger_detector() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def search_query(q: str) -> str:
        """Search query."""
        return f"result for {q}"

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Search 1",
                tool_calls=[ToolCall(id="c1", name="search_query", arguments={"q": "step1"})],
            ),
            LLMResponse(
                text="Search 2",
                tool_calls=[ToolCall(id="c2", name="search_query", arguments={"q": "step2"})],
            ),
            LLMResponse(text="Final answer: found everything!"),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5, max_consecutive_duplicate_tool_calls=2),
    )
    result = await agent.run(goal="Search steps")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "Final answer: found everything!"


@pytest.mark.asyncio
async def test_alternating_tools_do_not_trigger_detector() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def tool_a(x: int) -> int:
        """Tool A."""
        return x + 1

    @tool(registry=registry)
    def tool_b(x: int) -> int:
        """Tool B."""
        return x * 2

    # Alternate tool_a and tool_b with same args
    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="A 1",
                tool_calls=[ToolCall(id="c1", name="tool_a", arguments={"x": 1})],
            ),
            LLMResponse(
                text="B 1",
                tool_calls=[ToolCall(id="c2", name="tool_b", arguments={"x": 1})],
            ),
            LLMResponse(
                text="A 2",
                tool_calls=[ToolCall(id="c3", name="tool_a", arguments={"x": 1})],
            ),
            LLMResponse(text="Done"),
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=10, max_consecutive_duplicate_tool_calls=2),
    )
    result = await agent.run(goal="Alternating")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "Done"
