"""Unit tests for agent loop termination, max steps, and timeout guardrails."""

import asyncio

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.errors import MaxStepsExceeded, RunTimeoutError
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse, TokenUsage
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


@pytest.mark.asyncio
async def test_max_steps_exceeded_returns_status() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def step_tool(x: int) -> int:
        """Step tool."""
        return x + 1

    # 3 turns of tool calls, max_steps = 2
    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Calling tool 1",
                tool_calls=[ToolCall(id="call_1", name="step_tool", arguments={"x": 1})],
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            ),
            LLMResponse(
                text="Calling tool 2",
                tool_calls=[ToolCall(id="call_2", name="step_tool", arguments={"x": 2})],
                usage=TokenUsage(input_tokens=12, output_tokens=6),
            ),
            LLMResponse(
                text="Calling tool 3",
                tool_calls=[ToolCall(id="call_3", name="step_tool", arguments={"x": 3})],
                usage=TokenUsage(input_tokens=14, output_tokens=7),
            ),
        ]
    )

    agent = Agent(llm=llm, registry=registry, config=AgentConfig(max_steps=2))
    result = await agent.run(goal="Loop forever")

    assert result.status == RunStatus.MAX_STEPS_EXCEEDED
    assert result.steps_count == 2
    assert "Exceeded max steps (2)" in (result.failure_reason or "")
    assert result.final_answer is None
    assert result.total_input_tokens == 22
    assert result.total_output_tokens == 11


@pytest.mark.asyncio
async def test_max_steps_exceeded_raises_when_requested() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def dummy_tool_fail(x: int) -> int:
        """Dummy."""
        return x

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Call",
                tool_calls=[ToolCall(id="call_1", name="dummy_tool_fail", arguments={"x": 1})],
            )
        ]
    )
    agent = Agent(llm=llm, registry=registry, config=AgentConfig(max_steps=1))

    with pytest.raises(MaxStepsExceeded) as exc_info:
        await agent.run(goal="Fail on step count", raise_on_failure=True)

    assert exc_info.value.max_steps == 1
    assert "maximum execution steps" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_timeout_returns_status() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    async def slow_tool_timeout(delay: float) -> str:
        """Slow async tool."""
        await asyncio.sleep(delay)
        return "done"

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Calling slow tool",
                tool_calls=[ToolCall(id="c1", name="slow_tool_timeout", arguments={"delay": 0.2})],
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            )
        ]
    )

    # run_timeout_s is 0.05s while tool takes 0.2s
    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5, run_timeout_s=0.05, tool_timeout_s=1.0),
    )
    result = await agent.run(goal="Test timeout")

    assert result.status == RunStatus.TIMED_OUT
    assert result.final_answer is None
    assert "timed out" in (result.failure_reason or "").lower()
    assert result.steps_count >= 1


@pytest.mark.asyncio
async def test_run_timeout_raises_when_requested() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    async def slow_tool_raise(delay: float) -> str:
        """Slow async tool."""
        await asyncio.sleep(delay)
        return "done"

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Calling slow tool",
                tool_calls=[ToolCall(id="c1", name="slow_tool_raise", arguments={"delay": 0.2})],
            )
        ]
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5, run_timeout_s=0.05, tool_timeout_s=1.0),
    )

    with pytest.raises(RunTimeoutError) as exc_info:
        await agent.run(goal="Test timeout raising", raise_on_failure=True)

    assert exc_info.value.code == "RUN_TIMED_OUT"


@pytest.mark.asyncio
async def test_agent_handles_unexpected_exception() -> None:
    registry = ToolRegistry()
    # FakeLLMClient with empty queue raises RuntimeError
    llm = FakeLLMClient(responses=[])

    agent = Agent(llm=llm, registry=registry)
    result = await agent.run(goal="Handle crash")

    assert result.status == RunStatus.FAILED
    assert "response queue exhausted" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_agent_raises_unexpected_exception_when_flag_enabled() -> None:
    registry = ToolRegistry()
    llm = FakeLLMClient(responses=[])

    agent = Agent(llm=llm, registry=registry)
    with pytest.raises(RuntimeError):
        await agent.run(goal="Handle crash", raise_on_failure=True)
