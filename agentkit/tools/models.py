"""Neutral domain models for tools, tool execution results, and tool schemas."""

import base64
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

#: Key used by the persisted form of a binary thought signature. The wrapper makes the
#: encoding unambiguous: a bare string could be base64 or the raw bytes decoded as UTF-8.
THOUGHT_SIGNATURE_BASE64_KEY = "base64"


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

    @field_validator("thought_signature", mode="before")
    @classmethod
    def decode_thought_signature(cls, value: Any) -> bytes | None:
        """Decode a persisted signature back into bytes.

        Gemini signatures are arbitrary binary, so Pydantic's default UTF-8 handling of
        ``bytes`` cannot round-trip them. Persisted values are therefore an explicitly tagged
        base64 object. A bare string is accepted for histories written before that encoding
        existed: the raw bytes were stored as their UTF-8 decoding, which is recovered here.

        Args:
            value: Bytes, a ``{"base64": ...}`` object, a legacy string, or None.

        Returns:
            The signature bytes, or None.

        Raises:
            ValueError: If a tagged object does not carry a base64 string payload.
        """
        if value is None or isinstance(value, bytes):
            return value
        if isinstance(value, dict):
            encoded = value.get(THOUGHT_SIGNATURE_BASE64_KEY)
            if not isinstance(encoded, str):
                raise ValueError(
                    "thought_signature must be a base64 object "
                    f'{{"{THOUGHT_SIGNATURE_BASE64_KEY}": "..."}}'
                )
            return base64.b64decode(encoded.encode("ascii"), validate=True)
        if isinstance(value, str):
            return value.encode("utf-8")
        raise ValueError(f"Unsupported thought_signature value of type {type(value).__name__}")


class ToolSchema(BaseModel):
    """Neutral tool schema definition for export to LLM providers."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Tool identifier name.")
    description: str = Field(description="Documentation explaining tool behavior.")
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema specification of accepted parameters.",
    )
