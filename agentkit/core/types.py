"""Core enumeration types for AgentKit."""

from enum import StrEnum


class RunStatus(StrEnum):
    """Execution status of an agent run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    TIMED_OUT = "timed_out"


class Role(StrEnum):
    """Conversation participant role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
