"""Unit tests for StepTrace model, TraceSink interface, and InMemoryTraceSink."""

from datetime import datetime, timezone

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.trace import InMemoryTraceSink, StepTrace, TraceSink
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse, TokenUsage, ToolCall
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall as ExecToolCall
from agentkit.tools.registry import ToolRegistry, tool


def test_step_trace_initialization() -> None:
    now = datetime.now(timezone.utc)
    trace = StepTrace(
        run_id="run_123",
        step_no=1,
        tool_name="calculator",
        args={"expression": "2 + 2"},
        result={"ok": True, "output": "4"},
        error=None,
        latency_ms=15,
        input_tokens=100,
        output_tokens=25,
        timestamp=now,
    )

    assert trace.run_id == "run_123"
    assert trace.step_no == 1
    assert trace.tool_name == "calculator"
    assert trace.args == {"expression": "2 + 2"}
    assert trace.result == {"ok": True, "output": "4"}
    assert trace.latency_ms == 15
    assert trace.input_tokens == 100
    assert trace.output_tokens == 25
    assert trace.timestamp == now


def test_step_trace_default_timestamp() -> None:
    trace = StepTrace(
        run_id="run_abc",
        step_no=1,
        tool_name="test_tool",
    )
    assert trace.timestamp.tzinfo is not None
    assert trace.args == {}
    assert trace.result is None


@pytest.mark.asyncio
async def test_in_memory_trace_sink_crud() -> None:
    sink = InMemoryTraceSink()

    trace1 = StepTrace(run_id="run_1", step_no=1, tool_name="tool_a")
    trace2 = StepTrace(run_id="run_1", step_no=2, tool_name="tool_b")
    trace_other = StepTrace(run_id="run_2", step_no=1, tool_name="tool_c")

    await sink.record(trace2)
    await sink.record(trace1)
    await sink.record(trace_other)

    traces_run_1 = await sink.get_traces("run_1")
    assert len(traces_run_1) == 2
    # Verify ordered by step_no
    assert traces_run_1[0].step_no == 1
    assert traces_run_1[1].step_no == 2

    traces_run_2 = await sink.get_traces("run_2")
    assert len(traces_run_2) == 1
    assert traces_run_2[0].tool_name == "tool_c"

    assert await sink.get_traces("unknown_run") == []

    sink.clear()
    assert await sink.get_traces("run_1") == []


@pytest.mark.asyncio
async def test_agent_integration_with_trace_sink() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def add_numbers(a: int, b: int) -> int:
        """Add numbers."""
        return a + b

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Adding numbers",
                tool_calls=[ToolCall(id="c1", name="add_numbers", arguments={"a": 10, "b": 20})],
                usage=TokenUsage(input_tokens=50, output_tokens=15),
            ),
            LLMResponse(
                text="The sum is 30",
                usage=TokenUsage(input_tokens=80, output_tokens=10),
            ),
        ]
    )

    sink = InMemoryTraceSink()
    agent = Agent(llm=llm, registry=registry, trace=sink)

    result = await agent.run(goal="Add 10 and 20")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The sum is 30"

    traces = await sink.get_traces(result.run_id)
    assert len(traces) == 1
    assert traces[0].run_id == result.run_id
    assert traces[0].step_no == 1
    assert traces[0].tool_name == "add_numbers"
    assert traces[0].args == {"a": 10, "b": 20}
    assert traces[0].result is not None
    assert traces[0].result["output"] == "30"


@pytest.mark.asyncio
async def test_agent_continues_when_trace_sink_fails() -> None:
    registry = ToolRegistry()

    @tool(registry=registry)
    def simple_action() -> str:
        """Simple action."""
        return "ok"

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Action",
                tool_calls=[ToolCall(id="c1", name="simple_action", arguments={})],
            ),
            LLMResponse(text="Completed action"),
        ]
    )

    class BrokenTraceSink(TraceSink):
        async def record(self, trace: StepTrace) -> None:
            raise RuntimeError("Database connection lost in trace sink!")

        async def get_traces(self, run_id: str) -> list[StepTrace]:
            return []

    broken_sink = BrokenTraceSink()
    agent = Agent(llm=llm, registry=registry, trace=broken_sink)

    result = await agent.run(goal="Do simple action")
    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "Completed action"
