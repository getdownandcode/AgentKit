"""Abstract MemoryStore interface and in-memory test implementation."""

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from agentkit.core.types import RunStatus
from agentkit.llm.base import Message


class RunRecord(BaseModel):
    """Normalized snapshot representation of an agent execution run."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique run UUID.")
    session_id: str | None = Field(default=None, description="Optional associated session ID.")
    goal: str = Field(description="Goal or prompt executed.")
    status: RunStatus | str = Field(default=RunStatus.RUNNING, description="Current run status.")
    final_answer: str | None = Field(default=None, description="Final assistant response.")
    failure_reason: str | None = Field(
        default=None, description="Reason if run failed or timed out."
    )
    total_input_tokens: int = Field(default=0, ge=0, description="Total input tokens consumed.")
    total_output_tokens: int = Field(default=0, ge=0, description="Total output tokens generated.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Run initiation timestamp.",
    )
    finished_at: datetime | None = Field(default=None, description="Run completion timestamp.")


class MemoryStore(ABC):
    """Abstract interface defining operations for session message history and run state persistence."""

    @abstractmethod
    async def get_messages(self, session_id: str) -> list[Message]:
        """Load conversation messages for a session."""

    @abstractmethod
    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """Persist conversation messages for a session."""

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """Clear conversation history for a session."""

    @abstractmethod
    async def create_run(
        self,
        run_id: str,
        goal: str,
        session_id: str | None = None,
    ) -> None:
        """Create a new run record."""

    @abstractmethod
    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str,
        final_answer: str | None = None,
        failure_reason: str | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        """Update existing run state upon completion or failure."""

    @abstractmethod
    async def get_run(self, run_id: str) -> RunRecord | None:
        """Retrieve run record by run ID."""


class InMemoryMemoryStore(MemoryStore):
    """In-memory memory store implementation for testing and development."""

    def __init__(self) -> None:
        self._sessions: dict[str, list[Message]] = {}
        self._runs: dict[str, RunRecord] = {}

    async def get_messages(self, session_id: str) -> list[Message]:
        """Load conversation messages for a session."""
        messages = self._sessions.get(session_id, [])
        return [deepcopy(m) for m in messages]

    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """Persist conversation messages for a session."""
        self._sessions[session_id] = [deepcopy(m) for m in messages]

    async def clear_session(self, session_id: str) -> None:
        """Clear conversation history for a session."""
        self._sessions.pop(session_id, None)

    async def create_run(
        self,
        run_id: str,
        goal: str,
        session_id: str | None = None,
    ) -> None:
        """Create a new run record."""
        record = RunRecord(
            id=run_id,
            session_id=session_id,
            goal=goal,
            status=RunStatus.RUNNING,
            created_at=datetime.now(UTC),
        )
        self._runs[run_id] = record

    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str,
        final_answer: str | None = None,
        failure_reason: str | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        """Update existing run state upon completion or failure."""
        existing = self._runs.get(run_id)
        goal = existing.goal if existing else ""
        session_id = existing.session_id if existing else None
        created_at = existing.created_at if existing else datetime.now(UTC)

        updated = RunRecord(
            id=run_id,
            session_id=session_id,
            goal=goal,
            status=status,
            final_answer=final_answer,
            failure_reason=failure_reason,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            created_at=created_at,
            finished_at=datetime.now(UTC),
        )
        self._runs[run_id] = updated

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Retrieve run record by run ID."""
        return self._runs.get(run_id)

    def clear(self) -> None:
        """Clear all in-memory runs and sessions."""
        self._sessions.clear()
        self._runs.clear()
