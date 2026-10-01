"""Tiered dual-store persistence combining Redis session caching with PostgreSQL durable storage."""

from __future__ import annotations

import logging
from typing import Any

from agentkit.core.trace import StepTrace
from agentkit.core.types import RunStatus
from agentkit.llm.base import Message
from agentkit.memory.base import MemoryStore, RunRecord, SessionStore

logger = logging.getLogger(__name__)


class TieredMemoryStore(MemoryStore):
    """Combines an ephemeral SessionStore (e.g. Redis) for conversation histories
    with a durable Run repository (e.g. PostgreSQL) for run and step records.
    """

    def __init__(
        self,
        session_store: SessionStore,
        run_store: Any,
    ) -> None:
        self.session_store = session_store
        self.run_store = run_store

    async def get_messages(self, session_id: str) -> list[Message]:
        """Load conversation messages for a session from session store."""
        return await self.session_store.get_messages(session_id)

    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """Persist conversation messages for a session to session store."""
        await self.session_store.save_messages(session_id, messages)

    async def clear_session(self, session_id: str) -> None:
        """Clear conversation history for a session from session store."""
        await self.session_store.clear_session(session_id)

    async def create_run(
        self,
        run_id: str,
        goal: str,
        session_id: str | None = None,
    ) -> None:
        """Persist run initiation record in the run store."""
        if hasattr(self.run_store, "create_run"):
            await self.run_store.create_run(run_id=run_id, goal=goal, session_id=session_id)

    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str,
        final_answer: str | None = None,
        failure_reason: str | None = None,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        """Update existing run state in the run store."""
        if hasattr(self.run_store, "update_run"):
            await self.run_store.update_run(
                run_id=run_id,
                status=status,
                final_answer=final_answer,
                failure_reason=failure_reason,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
            )

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Retrieve run record from the run store."""
        if hasattr(self.run_store, "get_run"):
            record = await self.run_store.get_run(run_id)
            if isinstance(record, RunRecord):
                return record
        return None

    async def get_run_trace(self, run_id: str) -> list[StepTrace]:
        """Retrieve execution steps for a run from the run store.

        Forwarded so callers relying on duck-typed ``get_run_trace`` detection (such as the
        trace endpoint) read the durable ``steps`` table instead of silently falling back to a
        trace sink that may hold no rows.
        """
        getter = getattr(self.run_store, "get_run_trace", None)
        if getter is None:
            return []
        return list(await getter(run_id))

    async def list_runs(
        self,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        """List historical runs from the run store, optionally scoped to a session."""
        lister = getattr(self.run_store, "list_runs", None)
        if lister is None:
            return []
        return list(await lister(session_id=session_id, limit=limit))
