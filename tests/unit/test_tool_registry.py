"""Unit tests for tool registry and @tool decorator."""

import pytest

from agentkit.tools.models import ToolSchema
from agentkit.tools.registry import ToolRegistry, tool


def test_registry_register_and_lookup() -> None:
    """Verify manual registration and lookup in ToolRegistry."""
    registry = ToolRegistry()

    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    entry = registry.register(add)
    assert entry.name == "add"
    assert registry.has("add") is True
    assert registry.get("add") is entry
    assert registry.get("nonexistent") is None
    assert len(registry.schemas()) == 1
    assert isinstance(registry.schemas()[0], ToolSchema)
    assert registry.schemas()[0].name == "add"


def test_registry_duplicate_registration_raises() -> None:
    """Verify registering a duplicate tool name raises ValueError."""
    registry = ToolRegistry()

    def sample(x: str) -> str:
        return x

    registry.register(sample, name="same_name")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(sample, name="same_name")


def test_tool_decorator_bare_and_with_args() -> None:
    """Verify @tool decorator usage with and without arguments."""
    custom_registry = ToolRegistry()

    @tool(registry=custom_registry)
    def multiply(x: int, y: int) -> int:
        """Multiply two integers."""
        return x * y

    @tool(name="custom_div", description="Custom divider", registry=custom_registry)
    async def divide(x: float, y: float) -> float:
        return x / y

    assert custom_registry.has("multiply") is True
    assert custom_registry.has("custom_div") is True

    entry_mult = custom_registry.get("multiply")
    assert entry_mult is not None
    assert entry_mult.is_async is False

    entry_div = custom_registry.get("custom_div")
    assert entry_div is not None
    assert entry_div.is_async is True
    assert entry_div.schema.description == "Custom divider"

    # Decorated functions are still directly callable
    assert multiply(3, 4) == 12


@pytest.mark.asyncio
async def test_tool_decorator_async_direct_call() -> None:
    """Verify decorated async function can still be directly awaited."""
    custom_registry = ToolRegistry()

    @tool(registry=custom_registry)
    async def async_echo(msg: str) -> str:
        return f"echo: {msg}"

    res = await async_echo("hello")
    assert res == "echo: hello"
