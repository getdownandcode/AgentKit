"""Domain exception hierarchy for AgentKit."""

from typing import Any


class AgentKitError(Exception):
    """Base exception for all AgentKit domain errors."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        """Return structured dictionary representation for API serialization."""
        return {"code": self.code, "message": self.message}


class MaxStepsExceeded(AgentKitError):
    """Raised when an agent run exceeds the maximum allowed execution steps."""

    def __init__(self, max_steps: int) -> None:
        super().__init__(
            f"Agent reached maximum execution steps ({max_steps}) without producing a final answer.",
            code="MAX_STEPS_EXCEEDED",
        )
        self.max_steps = max_steps


class RunTimeoutError(AgentKitError):
    """Raised when an agent run exceeds its overall timeout duration."""

    def __init__(self, timeout_s: int | float) -> None:
        super().__init__(
            f"Agent run exceeded maximum execution timeout of {timeout_s} seconds.",
            code="RUN_TIMED_OUT",
        )
        self.timeout_s = timeout_s


class DuplicateToolCallLoopError(AgentKitError):
    """Raised when an agent run enters a repetitive loop calling identical tools with identical arguments."""

    def __init__(self, tool_name: str, count: int) -> None:
        super().__init__(
            f"Detected duplicate tool call loop: '{tool_name}' called {count} consecutive times with identical arguments.",
            code="DUPLICATE_TOOL_CALL_LOOP",
        )
        self.tool_name = tool_name
        self.count = count


class AuthenticationError(AgentKitError):
    """Raised when request API key is missing or invalid."""

    def __init__(self, message: str = "Invalid or missing API key.") -> None:
        super().__init__(message, code="UNAUTHORIZED")


class RateLimitExceededError(AgentKitError):
    """Raised when a client API key exceeds allowed request rate."""

    def __init__(self, retry_after: int = 60) -> None:
        super().__init__(
            f"Rate limit exceeded. Try again in {retry_after} seconds.",
            code="RATE_LIMIT_EXCEEDED",
        )
        self.retry_after = retry_after


class ToolError(AgentKitError):
    """Base exception for tool-related errors."""

    def __init__(self, tool_name: str, message: str, code: str = "TOOL_ERROR") -> None:
        super().__init__(message, code=code)
        self.tool_name = tool_name


class ToolNotFound(ToolError):
    """Raised when requested tool is not found in the registry."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            tool_name,
            f"Tool '{tool_name}' is not registered.",
            code="TOOL_NOT_FOUND",
        )


class ToolValidationError(ToolError):
    """Raised when tool arguments fail validation."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(
            tool_name,
            f"Validation failed for tool '{tool_name}': {message}",
            code="TOOL_VALIDATION_ERROR",
        )


class ToolExecutionError(ToolError):
    """Raised when tool execution produces an unexpected operational error."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(
            tool_name,
            f"Error executing tool '{tool_name}': {message}",
            code="TOOL_EXECUTION_ERROR",
        )


class ToolTimeoutError(ToolError):
    """Raised when a tool execution exceeds its per-tool timeout limit."""

    def __init__(self, tool_name: str, timeout_s: int) -> None:
        super().__init__(
            tool_name,
            f"Tool '{tool_name}' timed out after {timeout_s} seconds.",
            code="TOOL_TIMEOUT",
        )
        self.timeout_s = timeout_s


class LLMProviderError(AgentKitError):
    """Raised when an external LLM provider call fails."""

    def __init__(
        self,
        provider: str,
        message: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(
            f"LLM provider '{provider}' error: {message}",
            code="LLM_PROVIDER_ERROR",
        )
        self.provider = provider
        self.status_code = status_code


class ToolSecurityError(ToolError):
    """Raised when a tool operation violates sandboxing or security boundaries."""

    def __init__(self, message: str, tool_name: str = "security") -> None:
        super().__init__(tool_name, message, code="TOOL_SECURITY_ERROR")


class RunNotFoundError(AgentKitError):
    """Raised when a requested run is not found in the persistence store."""

    def __init__(self, run_id: str | Any) -> None:
        super().__init__(f"Run '{run_id}' not found.", code="RUN_NOT_FOUND")
        self.run_id = str(run_id)


# Aliases for convenience
ToolNotFoundError = ToolNotFound
LLMRateLimitError = RateLimitExceededError
