"""PostgreSQL persistence tests executed against a live PostgreSQL server.

These cover the behaviour that the SQLite-backed unit tests structurally cannot
assert: native UUID binding, JSON column round-tripping, TIMESTAMPTZ timezone
preservation and real foreign-key enforcement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentkit.core.pg_trace import PostgresTraceSink
from agentkit.core.trace import StepTrace
from agentkit.core.types import RunStatus
from agentkit.db.models import Run, Step
from agentkit.memory.pg_store import PostgresMemoryStore

pytestmark = pytest.mark.real_infra


async def test_run_and_step_round_trip_preserves_types(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """UUID, JSON and timestamptz values must survive a real round trip unchanged."""
    store = PostgresMemoryStore(session_factory=real_session_factory)
    run_id = str(uuid.uuid4())

    await store.create_run(run_id=run_id, goal="Round trip probe", session_id="sess-rt")
    await store.update_run(
        run_id=run_id,
        status=RunStatus.SUCCEEDED,
        final_answer="done",
        total_input_tokens=7,
        total_output_tokens=11,
    )

    record = await store.get_run(run_id)
    assert record is not None
    assert record.id == run_id
    assert record.status == RunStatus.SUCCEEDED
    assert record.total_input_tokens == 7
    assert record.total_output_tokens == 11

    # The ORM column is native UUID on PostgreSQL; confirm the stored value is a
    # real uuid column rather than text that merely parses.
    async with real_session_factory() as session:
        raw = await session.execute(
            text("SELECT pg_typeof(id)::text FROM runs WHERE id = :rid"),
            {"rid": uuid.UUID(run_id)},
        )
        assert raw.scalar_one() == "uuid"


async def test_json_columns_round_trip_nested_payloads(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Nested args/results must round-trip through PostgreSQL's JSON column type."""
    sink = PostgresTraceSink(session_factory=real_session_factory)
    run_id = str(uuid.uuid4())

    args: dict[str, object] = {
        "expression": "2 + 2",
        "nested": {"list": [1, 2, {"deep": True}], "null_value": None},
        "unicode": "café ✓",
    }
    result: dict[str, object] = {"output": "4", "meta": {"flags": ["ok", "fast"]}}
    created = datetime.now(UTC)

    await sink.record(
        StepTrace(
            run_id=run_id,
            step_no=1,
            tool_name="calculator",
            args=args,
            result=result,
            latency_ms=3,
            input_tokens=5,
            output_tokens=2,
            timestamp=created,
        )
    )

    traces = await sink.get_traces(run_id)
    assert len(traces) == 1
    assert traces[0].args == args
    assert traces[0].result == result
    assert traces[0].timestamp == created


async def test_trace_sink_creates_missing_parent_run(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Recording a step without a prior create_run must still satisfy the FK."""
    sink = PostgresTraceSink(session_factory=real_session_factory)
    run_id = str(uuid.uuid4())

    await sink.record(
        StepTrace(
            run_id=run_id,
            step_no=1,
            tool_name="read_file",
            args={"path": "notes.txt"},
            result={"output": "hello"},
            timestamp=datetime.now(UTC),
        )
    )

    async with real_session_factory() as session:
        parent = await session.get(Run, uuid.UUID(run_id))
        assert parent is not None, "PostgresTraceSink must auto-create the parent Run row"
        assert parent.status == "running"

    assert len(await sink.get_traces(run_id)) == 1


async def test_foreign_key_cascade_deletes_steps(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ON DELETE CASCADE must remove steps; SQLite does not enforce this by default."""
    sink = PostgresTraceSink(session_factory=real_session_factory)
    run_id = str(uuid.uuid4())

    for step_no in (1, 2, 3):
        await sink.record(
            StepTrace(
                run_id=run_id,
                step_no=step_no,
                tool_name="calculator",
                args={"expression": str(step_no)},
                result={"output": str(step_no)},
                timestamp=datetime.now(UTC),
            )
        )
    assert len(await sink.get_traces(run_id)) == 3

    async with real_session_factory() as session, session.begin():
        run = await session.get(Run, uuid.UUID(run_id))
        assert run is not None
        await session.delete(run)

    async with real_session_factory() as session:
        remaining = await session.execute(
            text("SELECT count(*) FROM steps WHERE run_id = :rid"),
            {"rid": uuid.UUID(run_id)},
        )
        assert remaining.scalar_one() == 0


async def test_orphan_step_is_rejected_by_postgres(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Inserting a step without a parent run must violate the FK constraint."""
    async with real_session_factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    Step(
                        id=uuid.uuid4(),
                        run_id=uuid.uuid4(),
                        step_no=1,
                        tool_name="calculator",
                        args={"expression": "1"},
                        created_at=datetime.now(UTC),
                    )
                )


async def test_list_runs_filters_by_session_and_orders_newest_first(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Session filtering and created_at ordering must hold on real PostgreSQL."""
    store = PostgresMemoryStore(session_factory=real_session_factory)

    ids: list[str] = []
    for index in range(3):
        run_id = str(uuid.uuid4())
        ids.append(run_id)
        await store.create_run(run_id=run_id, goal=f"goal {index}", session_id="sess-list")
        # Force a distinct, increasing created_at so the ordering assertion is stable.
        async with real_session_factory() as session, session.begin():
            run = await session.get(Run, uuid.UUID(run_id))
            assert run is not None
            run.created_at = datetime.now(UTC) + timedelta(seconds=index)

    runs = await store.list_runs(session_id="sess-list")
    assert [r.id for r in runs] == list(reversed(ids))

    other = str(uuid.uuid4())
    await store.create_run(run_id=other, goal="other session", session_id="sess-other")
    assert other not in {r.id for r in runs}

    limited = await store.list_runs(session_id="sess-list", limit=2)
    assert len(limited) == 2


async def test_non_uuid_run_id_maps_to_deterministic_uuid(
    real_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Non-UUID identifiers fall back to uuid5 so writes still land predictably."""
    store = PostgresMemoryStore(session_factory=real_session_factory)
    run_id = "run-without-dashes"

    await store.create_run(run_id=run_id, goal="deterministic mapping")
    record = await store.get_run(run_id)
    assert record is not None
    assert record.id == str(uuid.uuid5(uuid.NAMESPACE_DNS, run_id))
