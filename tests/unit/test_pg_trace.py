from datetime import UTC, datetime
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.core.agent import Agent
from agentkit.core.pg_trace import PostgresTraceSink
from agentkit.core.trace import StepTrace
from agentkit.core.types import RunStatus
from agentkit.db.models import Base, Run, Step
from agentkit.llm.base import LLMResponse, TokenUsage
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


@pytest.fixture
async def async_session_factory():
    """Create an in-memory SQLite async engine and sessionmaker for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_trace_sink_record_and_get(async_session_factory) -> None:
    sink = PostgresTraceSink(session_factory=async_session_factory)
    test_run_id = str(uuid.uuid4())

    # Step 1
    trace1 = StepTrace(
        run_id=test_run_id,
        step_no=1,
        tool_name="calculator",
        args={"expression": "10 * 5"},
        result={"ok": True, "output": "50"},
        error=None,
        latency_ms=12,
        input_tokens=40,
        output_tokens=10,
        timestamp=datetime.now(UTC),
    )
    # Step 2
    trace2 = StepTrace(
        run_id=test_run_id,
        step_no=2,
        tool_name="read_file",
        args={"path": "test.txt"},
        result={"ok": False},
        error="File not found",
        latency_ms=8,
        input_tokens=60,
        output_tokens=15,
        timestamp=datetime.now(UTC),
    )

    await sink.record(trace2)
    await sink.record(trace1)

    traces = await sink.get_traces(test_run_id)
    assert len(traces) == 2
    # Verify ordered by step_no
    assert traces[0].step_no == 1
    assert traces[0].tool_name == "calculator"
    assert traces[0].args == {"expression": "10 * 5"}
    assert traces[0].result == {"ok": True, "output": "50"}
    assert traces[0].error is None
    assert traces[0].latency_ms == 12

    assert traces[1].step_no == 2
    assert traces[1].tool_name == "read_file"
    assert traces[1].error == "File not found"
    assert traces[1].latency_ms == 8


@pytest.mark.asyncio
async def test_postgres_trace_sink_empty_traces(async_session_factory) -> None:
    sink = PostgresTraceSink(session_factory=async_session_factory)
    unknown_id = str(uuid.uuid4())
    traces = await sink.get_traces(unknown_id)
    assert traces == []


@pytest.mark.asyncio
async def test_agent_with_postgres_trace_sink(async_session_factory) -> None:
    sink = PostgresTraceSink(session_factory=async_session_factory)
    registry = ToolRegistry()

    @tool(registry=registry)
    def multiply(x: int, y: int) -> int:
        """Multiply x and y."""
        return x * y

    llm = FakeLLMClient(
        responses=[
            LLMResponse(
                text="Calling multiply",
                tool_calls=[ToolCall(id="c1", name="multiply", arguments={"x": 6, "y": 7})],
                usage=TokenUsage(input_tokens=30, output_tokens=12),
            ),
            LLMResponse(
                text="The product is 42",
                usage=TokenUsage(input_tokens=55, output_tokens=8),
            ),
        ]
    )

    agent = Agent(llm=llm, registry=registry, trace=sink)
    result = await agent.run(goal="What is 6 * 7?")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The product is 42"

    traces = await sink.get_traces(result.run_id)
    assert len(traces) == 1
    assert traces[0].run_id == result.run_id
    assert traces[0].step_no == 1
    assert traces[0].tool_name == "multiply"
    assert traces[0].args == {"x": 6, "y": 7}
    assert traces[0].result is not None
    assert traces[0].result["output"] == "42"
