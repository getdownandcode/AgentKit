import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.core.types import RunStatus
from agentkit.db.models import Base, Step
from agentkit.memory.pg_store import PostgresMemoryStore


@pytest.fixture
async def async_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create in-memory SQLite async engine and sessionmaker."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_memory_store_create_and_get(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresMemoryStore(session_factory=async_session_factory)
    run_id = str(uuid.uuid4())

    # Initial state
    assert await store.get_run(run_id) is None

    # Create run
    await store.create_run(run_id=run_id, goal="Test postgres store", session_id="session-42")

    record = await store.get_run(run_id)
    assert record is not None
    assert record.id == run_id
    assert record.session_id == "session-42"
    assert record.goal == "Test postgres store"
    assert record.status == RunStatus.RUNNING
    assert record.final_answer is None
    assert record.finished_at is None


@pytest.mark.asyncio
async def test_postgres_memory_store_update_success(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresMemoryStore(session_factory=async_session_factory)
    run_id = str(uuid.uuid4())

    await store.create_run(run_id=run_id, goal="Multiply numbers")
    await store.update_run(
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
        final_answer="Answer is 42",
        total_input_tokens=120,
        total_output_tokens=35,
    )

    record = await store.get_run(run_id)
    assert record is not None
    assert record.status == RunStatus.SUCCEEDED
    assert record.final_answer == "Answer is 42"
    assert record.total_input_tokens == 120
    assert record.total_output_tokens == 35
    assert record.finished_at is not None


@pytest.mark.asyncio
async def test_postgres_memory_store_update_failure(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresMemoryStore(session_factory=async_session_factory)
    run_id = str(uuid.uuid4())

    await store.create_run(run_id=run_id, goal="Failing task")
    await store.update_run(
        run_id=run_id,
        status=RunStatus.FAILED,
        failure_reason="External service unavailable",
        total_input_tokens=50,
        total_output_tokens=10,
    )

    record = await store.get_run(run_id)
    assert record is not None
    assert record.status == RunStatus.FAILED
    assert record.failure_reason == "External service unavailable"
    assert record.finished_at is not None


@pytest.mark.asyncio
async def test_postgres_memory_store_get_trace(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresMemoryStore(session_factory=async_session_factory)
    run_id = str(uuid.uuid4())
    run_uuid = uuid.UUID(run_id)

    await store.create_run(run_id=run_id, goal="Trace test")

    # Insert two steps
    async with async_session_factory() as session, session.begin():
        s1 = Step(
            id=uuid.uuid4(),
            run_id=run_uuid,
            step_no=1,
            tool_name="calculator",
            args={"expression": "1 + 1"},
            result={"output": "2"},
            latency_ms=10,
            input_tokens=20,
            output_tokens=5,
            created_at=datetime.now(UTC),
        )
        s2 = Step(
            id=uuid.uuid4(),
            run_id=run_uuid,
            step_no=2,
            tool_name="read_file",
            args={"path": "a.txt"},
            result={"output": "contents"},
            latency_ms=15,
            input_tokens=40,
            output_tokens=8,
            created_at=datetime.now(UTC),
        )
        session.add_all([s2, s1])

    traces = await store.get_run_trace(run_id)
    assert len(traces) == 2
    assert traces[0].step_no == 1
    assert traces[0].tool_name == "calculator"
    assert traces[1].step_no == 2
    assert traces[1].tool_name == "read_file"


@pytest.mark.asyncio
async def test_postgres_memory_store_list_runs(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = PostgresMemoryStore(session_factory=async_session_factory)
    sess_a = "session-A"
    sess_b = "session-B"

    r1 = str(uuid.uuid4())
    r2 = str(uuid.uuid4())
    r3 = str(uuid.uuid4())

    await store.create_run(run_id=r1, goal="Goal 1", session_id=sess_a)
    await store.create_run(run_id=r2, goal="Goal 2", session_id=sess_a)
    await store.create_run(run_id=r3, goal="Goal 3", session_id=sess_b)

    all_runs = await store.list_runs()
    assert len(all_runs) >= 3

    sess_a_runs = await store.list_runs(session_id=sess_a)
    assert len(sess_a_runs) == 2
    assert {r.id for r in sess_a_runs} == {r1, r2}
