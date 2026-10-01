"""Neutral domain models for tools, tool execution results, and tool schemas."""

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """Encapsulates the outcome of a single tool execution."""

    model_config = ConfigDict(frozen=True)

    ok: bool = Field(description="True if execution succeeded, False otherwise.")
    output: str = Field(default="", description="Text output of tool execution.")
    error: str | None = Field(default=None, description="Error message if failed.")
    truncated: bool = Field(
        default=False,
        description="Whether output was truncated due to length limits.",
    )
    latency_ms: int = Field(
        default=0,
        ge=0,
        description="Execution duration in milliseconds.",
    )

    @classmethod
    def success(
        cls,
        output: str,
        latency_ms: int = 0,
        truncated: bool = False,
    ) -> "ToolResult":
        """Create a successful ToolResult."""
        return cls(
            ok=True,
            output=output,
            error=None,
            truncated=truncated,
            latency_ms=latency_ms,
        )

    @classmethod
    def failure(
        cls,
        error: str,
        latency_ms: int = 0,
    ) -> "ToolResult":
        """Create a failed ToolResult."""
        return cls(
            ok=False,
            output="",
            error=error,
            truncated=False,
            latency_ms=latency_ms,
        )


class ToolCall(BaseModel):
    """Represents a request from an LLM to invoke a registered tool."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: str = Field(description="Unique identifier for the tool call instance.")
    name: str = Field(description="Target tool name.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("arguments", "args"),
        description="Parsed tool arguments.",
    )
    thought_signature: bytes | None = Field(
        default=None,
        description="Optional provider thought signature for preserving tool call state across turns.",
    )


class ToolSchema(BaseModel):
    """Neutral tool schema definition for export to LLM providers."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Tool identifier name.")
    description: str = Field(description="Documentation explaining tool behavior.")
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema specification of accepted parameters.",
    )
