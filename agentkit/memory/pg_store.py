"""PostgreSQL repository for durable Run and Step persistence."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentkit.core.trace import StepTrace
from agentkit.core.types import RunStatus
from agentkit.db.models import Run, Step
from agentkit.memory.base import RunRecord

logger = logging.getLogger(__name__)


class PostgresMemoryStore:
    """Async repository providing durable storage and query methods for runs and execution steps."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _parse_uuid(identifier: str) -> uuid.UUID:
        """Parse string to UUID, falling back to deterministic UUID if format differs."""
        try:
            return uuid.UUID(identifier)
        except ValueError:
            return uuid.uuid5(uuid.NAMESPACE_DNS, identifier)

    async def create_run(
        self,
        run_id: str,
        goal: str,
        session_id: str | None = None,
    ) -> None:
        """Persist an initial Run record in the database."""
        run_uuid = self._parse_uuid(run_id)
        async with self._session_factory() as session, session.begin():
            run = Run(
                id=run_uuid,
                session_id=session_id,
                goal=goal,
                status=str(RunStatus.RUNNING.value),
                created_at=datetime.now(UTC),
            )
            session.add(run)
            logger.debug("Created run %s in database", run_id)

    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str,
        final_answer: str | None = None,
        failure_reason: str | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        """Update existing Run record upon state transition or completion."""
        run_uuid = self._parse_uuid(run_id)
        status_str = status.value if isinstance(status, RunStatus) else str(status)

        async with self._session_factory() as session, session.begin():
            run = await session.get(Run, run_uuid)
            if run is None:
                logger.warning("Attempted to update non-existent run %s", run_id)
                return

            run.status = status_str
            run.final_answer = final_answer
            run.failure_reason = failure_reason
            run.total_input_tokens = total_input_tokens
            run.total_output_tokens = total_output_tokens
            run.finished_at = datetime.now(UTC)
            logger.debug("Updated run %s with status %s", run_id, status_str)

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Retrieve a single Run snapshot by ID."""
        run_uuid = self._parse_uuid(run_id)
        async with self._session_factory() as session:
            run = await session.get(Run, run_uuid)
            if run is None:
                return None

            return RunRecord(
                id=str(run.id),
                session_id=run.session_id,
                goal=run.goal,
                status=run.status,
                final_answer=run.final_answer,
                failure_reason=run.failure_reason,
                total_input_tokens=run.total_input_tokens,
                total_output_tokens=run.total_output_tokens,
                created_at=run.created_at,
                finished_at=run.finished_at,
            )

    async def get_run_trace(self, run_id: str) -> list[StepTrace]:
        """Retrieve ordered list of execution steps for a given run."""
        run_uuid = self._parse_uuid(run_id)
        async with self._session_factory() as session:
            stmt = select(Step).where(Step.run_id == run_uuid).order_by(Step.step_no.asc())
            result = await session.execute(stmt)
            steps = result.scalars().all()

            return [
                StepTrace(
                    run_id=str(step.run_id),
                    step_no=step.step_no,
                    tool_name=step.tool_name,
                    args=step.args or {},
                    result=step.result,
                    error=step.error,
                    latency_ms=step.latency_ms,
                    input_tokens=step.input_tokens,
                    output_tokens=step.output_tokens,
                    timestamp=step.created_at,
                )
                for step in steps
            ]

    async def list_runs(
        self,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        """List historical runs optionally filtered by session ID."""
        async with self._session_factory() as session:
            stmt = select(Run)
            if session_id is not None:
                stmt = stmt.where(Run.session_id == session_id)
            stmt = stmt.order_by(Run.created_at.desc()).limit(limit)

            result = await session.execute(stmt)
            runs = result.scalars().all()

            return [
                RunRecord(
                    id=str(r.id),
                    session_id=r.session_id,
                    goal=r.goal,
                    status=r.status,
                    final_answer=r.final_answer,
                    failure_reason=r.failure_reason,
                    total_input_tokens=r.total_input_tokens,
                    total_output_tokens=r.total_output_tokens,
                    created_at=r.created_at,
                    finished_at=r.finished_at,
                )
                for r in runs
            ]
