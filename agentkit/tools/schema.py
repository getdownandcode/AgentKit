"""Generates JSON schemas and Pydantic validation models from Python callables."""

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, Field, create_model

from agentkit.tools.models import ToolSchema


def create_tool_schema(
    func: Callable[..., Any],
    name: str | None = None,
    description: str | None = None,
) -> tuple[ToolSchema, type[BaseModel]]:
    """Inspect a Python function to produce a ToolSchema and Pydantic validation model.

    Args:
        func: The target function or coroutine to inspect.
        name: Optional override for the tool name.
        description: Optional override for the tool description.

    Returns:
        A tuple of (ToolSchema, Pydantic argument validation model).
    """
    tool_name = name or func.__name__

    # Extract description from docstring or fallback
    doc = inspect.getdoc(func)
    if description:
        tool_desc = description
    elif doc:
        tool_desc = doc.strip().split("\n\n")[0].replace("\n", " ").strip()
    else:
        tool_desc = f"Executes {tool_name} tool."

    sig = inspect.signature(func)
    type_hints = get_type_hints(func)

    fields: dict[str, tuple[Any, Any]] = {}

    for param_name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        param_type = type_hints.get(param_name, Any)

        if param.default is not inspect.Parameter.empty:
            fields[param_name] = (param_type, Field(default=param.default))
        else:
            fields[param_name] = (param_type, Field(...))

    # Dynamically construct Pydantic argument model
    args_model: type[BaseModel] = create_model(
        f"{tool_name.capitalize()}Args",
        **fields,  # type: ignore[call-overload]
    )

    raw_json_schema = args_model.model_json_schema()
    properties: dict[str, Any] = raw_json_schema.get("properties", {})
    required: list[str] = raw_json_schema.get("required", [])

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    schema = ToolSchema(
        name=tool_name,
        description=tool_desc,
        parameters=parameters,
    )

    return schema, args_model
