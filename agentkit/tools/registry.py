"""In-memory tool registry and @tool decorator for declarative tool definition."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel

from agentkit.tools.models import ToolSchema
from agentkit.tools.schema import create_tool_schema

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class ToolEntry:
    """Stores a registered tool callable along with its schema and argument model."""

    name: str
    func: Callable[..., Any]
    schema: ToolSchema
    args_model: type[BaseModel]
    is_async: bool


class ToolRegistry:
    """Thread-safe in-memory registry storing registered tools and their schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> ToolEntry:
        """Register a callable in the registry.

        Args:
            func: Function or coroutine to register.
            name: Optional override for the tool name.
            description: Optional override for description.

        Returns:
            The created ToolEntry.

        Raises:
            ValueError: If a tool with the given name is already registered.
        """
        tool_name = name or func.__name__
        if tool_name in self._tools:
            raise ValueError(f"Tool '{tool_name}' is already registered.")

        schema, args_model = create_tool_schema(func, name=tool_name, description=description)
        is_async = asyncio.iscoroutinefunction(func)

        entry = ToolEntry(
            name=tool_name,
            func=func,
            schema=schema,
            args_model=args_model,
            is_async=is_async,
        )
        self._tools[tool_name] = entry
        return entry

    def get(self, name: str) -> ToolEntry | None:
        """Retrieve a tool entry by name, or None if not found."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check whether a tool with the given name is registered."""
        return name in self._tools

    def schemas(self) -> list[ToolSchema]:
        """Return neutral schemas for all registered tools."""
        return [entry.schema for entry in self._tools.values()]

    def list_tools(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())


# Default global registry for convenience
default_registry = ToolRegistry()


@overload
def tool(func: F) -> F: ...


@overload
def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    registry: ToolRegistry | None = None,
) -> Callable[[F], F]: ...


def tool(
    func: F | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    registry: ToolRegistry | None = None,
) -> F | Callable[[F], F]:
    """Decorator to register a function or coroutine as an agent tool.

    Can be used with or without arguments:
        @tool
        def my_tool(x: int) -> int: ...

        @tool(name="custom_name", description="Custom desc")
        def my_tool(x: int) -> int: ...
    """
    target_registry = registry or default_registry

    def decorator(fn: F) -> F:
        entry = target_registry.register(fn, name=name, description=description)

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)

        target: Any = async_wrapper if entry.is_async else sync_wrapper
        target.__tool_entry__ = entry
        target.__tool_schema__ = entry.schema
        return cast(F, target)

    if func is not None:
        return decorator(func)

    return decorator
