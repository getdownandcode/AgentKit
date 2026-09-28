"""Step trace data models and abstract TraceSink interface."""

from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StepTrace(BaseModel):
    """Execution telemetry record captured for a single tool call step."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(description="UUID of the parent agent run.")
    step_no: int = Field(default=1, ge=1, description="Sequential step number.")
    tool_name: str = Field(description="Name of the executed tool.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments passed to the tool.",
    )
    result: dict[str, Any] | None = Field(
        default=None,
        description="Structured tool result data.",
    )
    error: str | None = Field(
        default=None,
        description="Operational error message if execution failed.",
    )
    latency_ms: int = Field(
        default=0,
        ge=0,
        description="Duration of tool execution in milliseconds.",
    )
    input_tokens: int = Field(
        default=0,
        ge=0,
        description="Input tokens consumed on this step.",
    )
    output_tokens: int = Field(
        default=0,
        ge=0,
        description="Output tokens generated on this step.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the step execution.",
    )


class TraceSink(ABC):
    """Abstract interface for storing and retrieving agent execution traces."""

    @abstractmethod
    async def record(self, trace: StepTrace) -> None:
        """Persist a single step trace record."""

    @abstractmethod
    async def get_traces(self, run_id: str) -> list[StepTrace]:
        """Retrieve all step traces for a given run ID, ordered by step number."""


class InMemoryTraceSink(TraceSink):
    """In-memory trace sink implementation for fast unit and integration testing."""

    def __init__(self) -> None:
        self._storage: dict[str, list[StepTrace]] = defaultdict(list)

    async def record(self, trace: StepTrace) -> None:
        """Store trace record in memory."""
        self._storage[trace.run_id].append(trace)

    async def get_traces(self, run_id: str) -> list[StepTrace]:
        """Return all traces for run_id sorted by step number."""
        traces = self._storage.get(run_id, [])
        return sorted(traces, key=lambda t: t.step_no)

    def clear(self) -> None:
        """Clear all in-memory traces."""
        self._storage.clear()
