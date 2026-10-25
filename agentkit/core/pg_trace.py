"""PostgreSQL-backed TraceSink implementation for persistent step telemetry."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentkit.core.trace import StepTrace, TraceSink
from agentkit.db.models import Run, Step

logger = logging.getLogger(__name__)


class PostgresTraceSink(TraceSink):
    """Trace sink that asynchronously persists StepTrace records to PostgreSQL database."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, trace: StepTrace) -> None:
        """Persist a single step trace to the steps table."""
        try:
            run_uuid = uuid.UUID(trace.run_id)
        except ValueError:
            logger.error(
                "Invalid UUID format for run_id: %s. Generating deterministic UUID.", trace.run_id
            )
            run_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, trace.run_id)

        async with self._session_factory() as session, session.begin():
            # A telemetry sink must never invent a business record. Without its own run row the
            # step has no parent for the foreign key, and a fabricated Run would report a
            # placeholder goal, a lost session id and a stale "running" status as if they were
            # real. The caller (Agent.run -> MemoryStore.create_run) owns Run creation, so a
            # missing parent here means the step is dropped, loudly.
            run = await session.get(Run, run_uuid)
            if run is None:
                logger.error(
                    "Dropping step %d for run %s: no parent run row exists; create the run "
                    "before recording its traces.",
                    trace.step_no,
                    trace.run_id,
                )
                return

            step_record = Step(
                id=uuid.uuid4(),
                run_id=run_uuid,
                step_no=trace.step_no,
                tool_name=trace.tool_name,
                args=trace.args,
                result=trace.result,
                error=trace.error,
                latency_ms=trace.latency_ms,
                input_tokens=trace.input_tokens,
                output_tokens=trace.output_tokens,
                created_at=trace.timestamp,
            )
            session.add(step_record)
            logger.debug(
                "Recorded step %d for run %s in PostgresTraceSink",
                trace.step_no,
                trace.run_id,
            )

    async def get_traces(self, run_id: str) -> list[StepTrace]:
        """Retrieve all step traces for a given run ID, ordered ascending by step number."""
        try:
            run_uuid = uuid.UUID(run_id)
        except ValueError:
            run_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, run_id)

        async with self._session_factory() as session:
            stmt = select(Step).where(Step.run_id == run_uuid).order_by(Step.step_no.asc())
            result = await session.execute(stmt)
            steps = result.scalars().all()

            return [
                StepTrace(
                    run_id=str(s.run_id),
                    step_no=s.step_no,
                    tool_name=s.tool_name,
                    args=s.args or {},
                    result=s.result,
                    error=s.error,
                    latency_ms=s.latency_ms,
                    input_tokens=s.input_tokens,
                    output_tokens=s.output_tokens,
                    timestamp=s.created_at,
                )
                for s in steps
            ]
