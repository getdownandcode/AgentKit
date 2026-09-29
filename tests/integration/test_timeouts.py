from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.types import RunStatus
from agentkit.db.models import Base
from agentkit.llm.base import LLMResponse, Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.tools.models import ToolCall, ToolResult
from agentkit.tools.registry import ToolRegistry, tool


@pytest.mark.asyncio
async def test_hanging_tool_triggers_tool_timeout_and_agent_recovers() -> None:
    """Verify that a tool hanging beyond tool_timeout_s fails gracefully without terminating agent."""
    registry = ToolRegistry()

    @tool(name="hanging_tool", description="A tool that sleeps longer than timeout.")
    async def hanging_tool(duration_s: float = 0.5) -> ToolResult:
        await asyncio.sleep(duration_s)
        return ToolResult(output="done", ok=True)

    registry.register(hanging_tool)

    fake_llm = FakeLLMClient(
        responses=[
            # Step 1: LLM invokes hanging tool
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="call_slow_1", name="hanging_tool", arguments={"duration_s": 0.5})
                ],
            ),
            # Step 2: LLM receives timeout error and provides fallback answer
            LLMResponse(
                text="The tool timed out, but I recovered and produced this answer.",
                tool_calls=[],
            ),
        ]
    )

    config = AgentConfig(
        tool_timeout_s=0.05,  # Short tool timeout: 50ms
        run_timeout_s=2.0,  # Generous overall run timeout: 2s
        max_steps=5,
    )
    memory_store = InMemoryMemoryStore()

    agent = Agent(
        llm=fake_llm,
        registry=registry,
        config=config,
        memory=memory_store,
    )

    result = await agent.run(goal="Test slow tool handling")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The tool timed out, but I recovered and produced this answer."
    assert result.steps_count == 2

    # Check conversation history received the tool timeout error
    tool_msgs = [m for m in fake_llm.history[-1] if m.role.value == "tool"]
    assert len(tool_msgs) == 1
    assert "timed out" in tool_msgs[0].content.lower()


@pytest.mark.asyncio
async def test_hanging_run_triggers_global_timeout_and_persists_status() -> None:
    """Verify that an overall run exceeding run_timeout_s terminates as TIMED_OUT in store."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    pg_memory = PostgresMemoryStore(session_factory)

    # LLM simulates a hang longer than run_timeout_s
    class HangingLLM(FakeLLMClient):
        async def chat(
            self,
            messages: list[Message],
            tools: list[Any] | None = None,
        ) -> LLMResponse:
            _ = (messages, tools)
            await asyncio.sleep(0.3)
            return LLMResponse(text="Delayed response")

    hanging_llm = HangingLLM()
    config = AgentConfig(
        run_timeout_s=0.08,  # Run timeout: 80ms
        max_steps=5,
    )

    agent = Agent(
        llm=hanging_llm,
        registry=ToolRegistry(),
        config=config,
        memory=pg_memory,
    )

    # Run with raise_on_failure=False
    result = await agent.run(goal="Long running task", raise_on_failure=False)

    assert result.status == RunStatus.TIMED_OUT
    assert "timed out" in (result.failure_reason or "").lower()

    # Verify status is persisted in PostgresMemoryStore
    stored_run = await pg_memory.get_run(result.run_id)
    assert stored_run is not None
    assert stored_run.status == RunStatus.TIMED_OUT
    assert stored_run.failure_reason is not None
    assert "timed out" in stored_run.failure_reason.lower()

    await engine.dispose()
