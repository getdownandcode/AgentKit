"""Safe execution engine for running registered tools with isolation and limits."""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agentkit.tools.models import ToolCall, ToolResult

if TYPE_CHECKING:
    from agentkit.tools.registry import ToolRegistry


async def execute_tool(
    registry: "ToolRegistry",
    tool_call: ToolCall,
    default_timeout_s: float = 15.0,
    max_chars: int = 2000,
) -> ToolResult:
    """Safely execute a tool call against the registry.

    Enforces argument validation, timeouts, sync/async dispatch, output truncation,
    and catches all exceptions without raising into the caller.

    Args:
        registry: The ToolRegistry containing registered tools.
        tool_call: The tool call request with name and arguments.
        default_timeout_s: Maximum execution duration allowed in seconds.
        max_chars: Maximum character length for output before truncation.

    Returns:
        Structured ToolResult.
    """
    start_time = time.perf_counter()

    def get_latency_ms() -> int:
        return max(0, int((time.perf_counter() - start_time) * 1000))

    # 1. Lookup tool in registry
    entry = registry.get(tool_call.name)
    if entry is None:
        return ToolResult.failure(
            f"Tool '{tool_call.name}' is not registered.",
            latency_ms=get_latency_ms(),
        )

    # 2. Validate arguments with dynamic Pydantic model
    try:
        validated_args = entry.args_model(**tool_call.arguments)
        kwargs = validated_args.model_dump()
    except ValidationError as exc:
        return ToolResult.failure(
            f"Validation error for tool '{tool_call.name}': {exc}",
            latency_ms=get_latency_ms(),
        )
    except Exception as exc:
        return ToolResult.failure(
            f"Argument parsing error for tool '{tool_call.name}': {exc}",
            latency_ms=get_latency_ms(),
        )

    # 3. Execute tool with per-tool timeout
    try:
        async with asyncio.timeout(default_timeout_s):
            if entry.is_async:
                raw_output: Any = await entry.func(**kwargs)
            else:
                raw_output = await asyncio.to_thread(entry.func, **kwargs)

        latency_ms = get_latency_ms()

        # Format output as string
        output_str = raw_output if isinstance(raw_output, str) else str(raw_output)

        # Truncate if output exceeds max_chars limit
        if len(output_str) > max_chars:
            truncated_str = (
                output_str[:max_chars]
                + f"\n[Output truncated: exceeded {max_chars} character limit]"
            )
            return ToolResult.success(
                truncated_str,
                latency_ms=latency_ms,
                truncated=True,
            )

        return ToolResult.success(
            output_str,
            latency_ms=latency_ms,
            truncated=False,
        )

    except TimeoutError:
        return ToolResult.failure(
            f"Tool '{tool_call.name}' timed out after {default_timeout_s}s.",
            latency_ms=get_latency_ms(),
        )
    except Exception as exc:
        return ToolResult.failure(
            f"Error executing tool '{tool_call.name}': {exc}",
            latency_ms=get_latency_ms(),
        )
