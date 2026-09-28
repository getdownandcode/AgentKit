"""Unit tests for safe tool execution engine."""

import asyncio
import time

import pytest

from agentkit.tools.executor import execute_tool
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


@pytest.fixture
def test_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def sync_add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    async def async_greet(name: str) -> str:
        """Greet a user asynchronously."""
        await asyncio.sleep(0.01)
        return f"Hello, {name}!"

    def slow_tool(duration: float) -> str:
        """Simulate a slow synchronous tool."""
        time.sleep(duration)
        return "finished"

    def error_tool() -> str:
        """A tool that raises an unexpected exception."""
        raise RuntimeError("Disk write failure")

    def verbose_tool(length: int) -> str:
        """Generate long text."""
        return "X" * length

    registry.register(sync_add)
    registry.register(async_greet)
    registry.register(slow_tool)
    registry.register(error_tool)
    registry.register(verbose_tool)
    return registry


@pytest.mark.asyncio
async def test_execute_sync_tool_success(test_registry: ToolRegistry) -> None:
    """Verify executing synchronous tool in threadpool."""
    call = ToolCall(id="call_1", name="sync_add", arguments={"a": 10, "b": 25})
    result = await execute_tool(test_registry, call)
    assert result.ok is True
    assert result.output == "35"
    assert result.error is None
    assert result.truncated is False
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_execute_async_tool_success(test_registry: ToolRegistry) -> None:
    """Verify executing async tool."""
    call = ToolCall(id="call_2", name="async_greet", arguments={"name": "Alice"})
    result = await execute_tool(test_registry, call)
    assert result.ok is True
    assert result.output == "Hello, Alice!"
    assert result.latency_ms >= 10


@pytest.mark.asyncio
async def test_execute_unknown_tool(test_registry: ToolRegistry) -> None:
    """Verify unknown tool returns structured failure without raising."""
    call = ToolCall(id="call_3", name="nonexistent", arguments={})
    result = await execute_tool(test_registry, call)
    assert result.ok is False
    assert "not registered" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_validation_error(test_registry: ToolRegistry) -> None:
    """Verify argument validation failure returns structured failure."""
    call = ToolCall(id="call_4", name="sync_add", arguments={"a": "not_an_int"})
    result = await execute_tool(test_registry, call)
    assert result.ok is False
    assert "Validation error" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_tool_timeout(test_registry: ToolRegistry) -> None:
    """Verify execution timeout returns structured failure."""
    call = ToolCall(id="call_5", name="slow_tool", arguments={"duration": 0.5})
    result = await execute_tool(test_registry, call, default_timeout_s=0.1)
    assert result.ok is False
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_tool_exception_caught(test_registry: ToolRegistry) -> None:
    """Verify operational exception inside tool is caught and returned as structured error."""
    call = ToolCall(id="call_6", name="error_tool", arguments={})
    result = await execute_tool(test_registry, call)
    assert result.ok is False
    assert "Disk write failure" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_output_truncation(test_registry: ToolRegistry) -> None:
    """Verify large outputs are truncated to max_chars limit."""
    call = ToolCall(id="call_7", name="verbose_tool", arguments={"length": 500})
    result = await execute_tool(test_registry, call, max_chars=100)
    assert result.ok is True
    assert result.truncated is True
    assert len(result.output) <= 150  # 100 chars + truncation marker
    assert "[Output truncated" in result.output
