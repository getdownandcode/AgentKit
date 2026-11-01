"""Regression tests for cancellation finalization (PR #122 review finding).

A cancellation can land on any awaited step that follows create_run(): the
session-history load before the reasoning loop, or an await inside the loop. In
both windows the run row already exists, so the run must reach a terminal canceled
status before the exception propagates; once the loop is running, the session
history must also be repaired so no orphaned assistant tool call stays stored.
"""

from __future__ import annotations

import asyncio

import pytest

from agentkit.core.agent import _ABORTED_TOOL_RESULT_CONTENT, Agent, AgentConfig
from agentkit.core.types import Role, RunStatus
from agentkit.llm.base import Message
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore
from agentkit.tools.registry import ToolRegistry


class _RecordingStore(InMemoryMemoryStore):
    """In-memory store recording the writes the agent performs during a run."""

    def __init__(self) -> None:
        super().__init__()
        self.update_statuses: list[RunStatus] = []
        self.save_calls: list[list[Message]] = []

    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str,
        final_answer: str | None = None,
        failure_reason: str | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        """Record the requested status, then delegate to the in-memory store."""
        self.update_statuses.append(RunStatus(status))
        await super().update_run(
            run_id,
            status,
            final_answer=final_answer,
            failure_reason=failure_reason,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
        )

    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """Record each saved snapshot, then delegate to the in-memory store."""
        self.save_calls.append(list(messages))
        await super().save_messages(session_id, messages)


class _SuspendingHistoryStore(_RecordingStore):
    """Store whose get_messages() stays pending until the test opens the gate.

    A pending first load reproduces the review finding: the await starts after
    create_run() persisted the run row but before the reasoning loop — where the
    inner CancelledError handler historically did not even exist. Opening the gate
    after cancellation lets the test read the untouched history via the public API.
    """

    def __init__(self) -> None:
        super().__init__()
        self.run_created = asyncio.Event()
        self._history_gate = asyncio.Event()

    def release_history(self) -> None:
        """Open the history gate so the test can read the store back after cancellation."""
        self._history_gate.set()

    async def create_run(self, run_id: str, goal: str, session_id: str | None = None) -> None:
        """Signal that the run row exists, mirroring the persisted 'running' state."""
        await super().create_run(run_id, goal, session_id=session_id)
        self.run_created.set()

    async def get_messages(self, session_id: str) -> list[Message]:
        """Stay pending until the gate opens, then load through the in-memory store."""
        await self._history_gate.wait()
        return await super().get_messages(session_id)


@pytest.mark.asyncio
async def test_cancellation_during_history_load_finalizes_run_canceled() -> None:
    """Cancel while get_messages() awaits: terminal write happens before re-raise.

    The gate opens only after create_run() persisted the row, so the cancellation is
    delivered in exactly the window the review flagged: after the run exists and
    before the handler guarded by the reasoning loop.
    """
    store = _SuspendingHistoryStore()
    session_id = "sess-cancel-setup"
    history = [Message.user("Earlier conversation.")]
    await store.save_messages(session_id, history)
    # The pre-run seed writes through the same spy: the run being tested has not
    # started yet, so its own save activity starts from an empty recording.
    store.save_calls.clear()

    agent = Agent(
        llm=FakeLLMClient(),
        registry=ToolRegistry(),
        memory=store,
        config=AgentConfig(max_steps=3),
    )

    task = asyncio.create_task(agent.run(goal="Do the thing.", session_id=session_id))
    await asyncio.wait_for(store.run_created.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Exactly one terminal write, and it is the canceled one: nothing between
    # create_run and the guard could have marked the run failed or finished.
    assert store.update_statuses == [RunStatus.CANCELED]
    # History was still loading, so the local list is empty: saving it would
    # overwrite the stored session with nothing instead of repairing it.
    assert store.save_calls == []
    store.release_history()
    assert await store.get_messages(session_id) == history


@pytest.mark.asyncio
async def test_cancellation_during_tool_execution_repairs_history() -> None:
    """Cancel while a tool executes: canceled status plus repaired history persist."""
    started = asyncio.Event()
    gate = asyncio.Event()
    registry = ToolRegistry()

    async def blocking_tool() -> str:
        """Hold the reasoning step open until the run is cancelled."""
        started.set()
        await gate.wait()
        return "unreachable: the run is cancelled before the tool completes"

    registry.register(blocking_tool)

    store = _RecordingStore()
    llm = FakeLLMClient()
    llm.queue_tool_call(
        tool_name="blocking_tool",
        arguments={},
        call_id="call-blocked",
        text="Calling the tool.",
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        memory=store,
        config=AgentConfig(max_steps=3),
    )

    task = asyncio.create_task(agent.run(goal="Use the tool.", session_id="sess-cancel-loop"))
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.update_statuses == [RunStatus.CANCELED]
    assert len(store.save_calls) == 1
    snapshot = store.save_calls[0]
    assert [m.role for m in snapshot] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL]
    # The categorical repair rule: every requested tool call keeps a matching tool
    # result, so the next run in this session gets a provider-valid history.
    assert snapshot[-2].tool_calls is not None and snapshot[-2].tool_calls[0].id == "call-blocked"
    assert snapshot[-1].tool_call_id == "call-blocked"
    assert snapshot[-1].content == _ABORTED_TOOL_RESULT_CONTENT
