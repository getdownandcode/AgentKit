"""Pydantic v2 schemas for API request and response boundaries."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    """Payload to initiate a new agent execution run."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1, description="Goal or task description for the agent.")
    session_id: str | None = Field(
        default=None,
        description="Optional conversation session ID to preserve context across multiple runs.",
    )


class RunResponse(BaseModel):
    """Summary of an agent execution run."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str = Field(..., description="Unique identifier of the run.")
    session_id: str | None = Field(default=None, description="Associated session identifier.")
    goal: str = Field(..., description="Task objective submitted for this run.")
    status: str = Field(..., description="Execution status of the run.")
    answer: str | None = Field(default=None, description="Final answer produced by the agent.")
    failure_reason: str | None = Field(
        default=None, description="Reason for failure if status is failed or timed_out."
    )
    total_input_tokens: int = Field(default=0, description="Total input tokens consumed.")
    total_output_tokens: int = Field(default=0, description="Total output tokens generated.")
    created_at: datetime = Field(..., description="Timestamp when the run was initiated.")
    finished_at: datetime | None = Field(
        default=None, description="Timestamp when the run completed."
    )


class StepTraceResponse(BaseModel):
    """Telemetry record for a single tool execution step within a run."""

    model_config = ConfigDict(from_attributes=True)

    step_no: int = Field(..., description="Sequential step index starting from 1.")
    tool_name: str = Field(..., description="Name of the invoked tool.")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments passed to the tool.")
    result: Any = Field(default=None, description="Result payload returned by the tool.")
    error: str | None = Field(default=None, description="Error message if execution failed.")
    latency_ms: int = Field(default=0, description="Execution duration in milliseconds.")
    input_tokens: int = Field(default=0, description="Tokens used for this step prompt.")
    output_tokens: int = Field(default=0, description="Tokens produced in this step completion.")
    timestamp: datetime = Field(..., description="Timestamp when step occurred.")


class RunTraceResponse(BaseModel):
    """Complete chronological step trace for a given run."""

    run_id: str = Field(..., description="Unique run identifier.")
    steps: list[StepTraceResponse] = Field(
        default_factory=list, description="Chronological sequence of step traces."
    )


class ToolSchemaResponse(BaseModel):
    """Schema specification for an individual registered tool."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Unique tool identifier name.")
    description: str = Field(..., description="Description of tool functionality.")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON schema describing tool parameter structure.",
    )


class ToolsListResponse(BaseModel):
    """List of all registered tools and their input schemas."""

    model_config = ConfigDict(frozen=True)

    tools: list[ToolSchemaResponse] = Field(default_factory=list, description="Registered tools.")


class HealthCheckResponse(BaseModel):
    """Liveness and dependency connectivity status report."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(..., description="'healthy' or 'unhealthy'.")
    database: str = Field(..., description="'healthy', 'unhealthy', or 'disabled'.")
    redis: str = Field(..., description="'healthy', 'unhealthy', or 'disabled'.")
    details: dict[str, str] = Field(
        default_factory=dict, description="Detailed diagnostic messages if degraded."
    )
