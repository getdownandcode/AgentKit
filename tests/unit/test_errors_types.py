"""Unit tests for domain errors and core enums."""

from agentkit.core.errors import (
    AgentKitError,
    AuthenticationError,
    LLMProviderError,
    MaxStepsExceeded,
    RateLimitExceededError,
    RunTimeoutError,
    ToolExecutionError,
    ToolNotFound,
    ToolValidationError,
)
from agentkit.core.types import Role, RunStatus


def test_core_enums() -> None:
    """Verify RunStatus and Role enum values and string behavior."""
    assert RunStatus.PENDING.value == "pending"
    assert RunStatus.RUNNING.value == "running"
    assert RunStatus.SUCCEEDED.value == "succeeded"
    assert RunStatus.FAILED.value == "failed"
    assert RunStatus.MAX_STEPS_EXCEEDED.value == "max_steps_exceeded"
    assert RunStatus.TIMED_OUT.value == "timed_out"

    assert Role.SYSTEM.value == "system"
    assert Role.USER.value == "user"
    assert Role.ASSISTANT.value == "assistant"
    assert Role.TOOL.value == "tool"

    # Verify string representation
    assert str(Role.SYSTEM) == "system"
    assert str(RunStatus.SUCCEEDED) == "succeeded"


def test_agentkit_error_hierarchy_and_serialization() -> None:
    """Verify base AgentKitError and subclass serialization."""
    base_err = AgentKitError("Something failed", code="CUSTOM_ERROR")
    assert str(base_err) == "Something failed"
    assert base_err.to_dict() == {"code": "CUSTOM_ERROR", "message": "Something failed"}

    max_steps_err = MaxStepsExceeded(15)
    assert max_steps_err.max_steps == 15
    assert "15" in str(max_steps_err)
    assert max_steps_err.code == "MAX_STEPS_EXCEEDED"

    tool_nf = ToolNotFound("missing_tool")
    assert tool_nf.tool_name == "missing_tool"
    assert tool_nf.code == "TOOL_NOT_FOUND"

    tool_val = ToolValidationError("calc", "Invalid expression")
    assert tool_val.tool_name == "calc"
    assert tool_val.code == "TOOL_VALIDATION_ERROR"

    tool_exec = ToolExecutionError("calc", "Division by zero")
    assert tool_exec.code == "TOOL_EXECUTION_ERROR"

    llm_err = LLMProviderError("gemini", "Quota exceeded", status_code=429)
    assert llm_err.provider == "gemini"
    assert llm_err.status_code == 429
    assert llm_err.code == "LLM_PROVIDER_ERROR"

    timeout_err = RunTimeoutError(60)
    assert timeout_err.timeout_s == 60
    assert timeout_err.code == "RUN_TIMED_OUT"

    auth_err = AuthenticationError()
    assert auth_err.code == "UNAUTHORIZED"

    rate_err = RateLimitExceededError(retry_after=45)
    assert rate_err.retry_after == 45
    assert rate_err.code == "RATE_LIMIT_EXCEEDED"
