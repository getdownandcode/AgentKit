import pytest

from agentkit.core.types import Role, RunStatus
from agentkit.llm.base import Message
from agentkit.memory.base import InMemoryMemoryStore, MemoryStore, RunRecord


def test_run_record_model() -> None:
    record = RunRecord(
        id="run-1",
        session_id="session-1",
        goal="Calculate total",
        status=RunStatus.RUNNING,
    )
    assert record.id == "run-1"
    assert record.session_id == "session-1"
    assert record.goal == "Calculate total"
    assert record.status == RunStatus.RUNNING
    assert record.final_answer is None
    assert record.total_input_tokens == 0
    assert record.created_at is not None


@pytest.mark.asyncio
async def test_in_memory_memory_store_sessions() -> None:
    store: MemoryStore = InMemoryMemoryStore()

    # Empty session returns []
    messages = await store.get_messages("sess-1")
    assert messages == []

    # Save messages
    msg1 = Message.user("Hello agent")
    msg2 = Message.assistant("Hello user")
    await store.save_messages("sess-1", [msg1, msg2])

    loaded = await store.get_messages("sess-1")
    assert len(loaded) == 2
    assert loaded[0].role == Role.USER
    assert loaded[0].content == "Hello agent"
    assert loaded[1].role == Role.ASSISTANT
    assert loaded[1].content == "Hello user"

    # Append additional message
    msg3 = Message.user("What is 2+2?")
    await store.save_messages("sess-1", [msg1, msg2, msg3])
    loaded = await store.get_messages("sess-1")
    assert len(loaded) == 3

    # Clear session
    await store.clear_session("sess-1")
    assert await store.get_messages("sess-1") == []


@pytest.mark.asyncio
async def test_in_memory_memory_store_runs() -> None:
    store: MemoryStore = InMemoryMemoryStore()

    # Unknown run returns None
    assert await store.get_run("run-xyz") is None

    # Create run
    await store.create_run(run_id="run-100", goal="Summarize file", session_id="sess-abc")
    run = await store.get_run("run-100")
    assert run is not None
    assert run.id == "run-100"
    assert run.goal == "Summarize file"
    assert run.session_id == "sess-abc"
    assert run.status == RunStatus.RUNNING
    assert run.final_answer is None

    # Update run
    await store.update_run(
        run_id="run-100",
        status=RunStatus.SUCCEEDED,
        final_answer="Summary complete",
        total_input_tokens=150,
        total_output_tokens=40,
    )

    updated = await store.get_run("run-100")
    assert updated is not None
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.final_answer == "Summary complete"
    assert updated.total_input_tokens == 150
    assert updated.total_output_tokens == 40
    assert updated.finished_at is not None
