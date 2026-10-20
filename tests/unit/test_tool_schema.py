"""Unit tests for tool schema generation from callable inspection."""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.http_fetch import http_fetch
from agentkit.tools.builtin.read_file import read_file
from agentkit.tools.builtin.sql_readonly import sql_readonly
from agentkit.tools.builtin.web_search import web_search
from agentkit.tools.schema import create_tool_schema


def dummy_calculator(expression: str) -> str:
    """Evaluate a mathematical expression safely.

    Args:
        expression: The arithmetic string to evaluate.
    """
    return expression


def dummy_search(query: str, limit: int = 5, verbose: bool = False) -> str:
    """Search for relevant documents."""
    return f"{query}:{limit}:{verbose}"


def dummy_no_docstring(x: float, y: str | None = None) -> float:
    return x if y is None else x + len(y)


def test_create_tool_schema_basic() -> None:
    """Verify tool schema creation for single-argument function with docstring."""
    schema, args_model = create_tool_schema(dummy_calculator)
    assert schema.name == "dummy_calculator"
    assert "Evaluate a mathematical expression safely" in schema.description
    assert schema.parameters["type"] == "object"
    assert "expression" in schema.parameters["properties"]
    assert schema.parameters["properties"]["expression"]["type"] == "string"
    assert schema.parameters["required"] == ["expression"]

    # Test args_model validation
    validated = args_model(expression="1 + 1")
    assert validated.model_dump() == {"expression": "1 + 1"}


def test_create_tool_schema_with_defaults_and_optionals() -> None:
    """Verify optional parameters and defaults are reflected correctly in required list."""
    schema, args_model = create_tool_schema(dummy_search)
    assert schema.name == "dummy_search"
    assert schema.parameters["required"] == ["query"]
    assert "limit" in schema.parameters["properties"]
    assert "verbose" in schema.parameters["properties"]

    # Validate defaults
    validated = args_model(query="ai agents")
    assert validated.model_dump() == {"query": "ai agents", "limit": 5, "verbose": False}


def test_create_tool_schema_missing_docstring_fallback() -> None:
    """Verify clean fallback description when docstring is absent."""
    schema, args_model = create_tool_schema(dummy_no_docstring)
    assert schema.name == "dummy_no_docstring"
    assert schema.description != ""
    assert "x" in schema.parameters["required"]
    assert "y" not in schema.parameters.get("required", [])


def test_create_tool_schema_custom_name_and_description() -> None:
    """Verify custom name and description overrides."""
    schema, _ = create_tool_schema(
        dummy_calculator,
        name="custom_calc",
        description="Custom description",
    )
    assert schema.name == "custom_calc"
    assert schema.description == "Custom description"


class QueryMode(StrEnum):
    QUICK = "quick"
    EXHAUSTIVE = "exhaustive"


class FilterConfig(BaseModel):
    max_results: int = Field(default=10, description="Maximum number of results to fetch.")
    include_raw: bool = False


def tool_with_enum_and_nested_model(
    query: str,
    mode: QueryMode = QueryMode.QUICK,
    filter_config: FilterConfig | None = None,
) -> str:
    """Tool that utilizes Enum and nested BaseModel schemas."""
    return f"{query}:{mode}:{filter_config}"


def _assert_all_refs_resolve(schema: dict[str, Any]) -> None:
    """Validate that every JSON Pointer in a $ref successfully resolves inside the schema."""
    refs: list[str] = []

    def _collect_refs(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    refs.append(v)
                else:
                    _collect_refs(v)
        elif isinstance(node, list):
            for item in node:
                _collect_refs(item)

    _collect_refs(schema)
    assert refs, "Expected schema to contain $ref references to test"

    for ref in refs:
        assert ref.startswith("#/"), f"Expected URI fragment reference, got {ref}"
        parts = ref[2:].split("/")
        target: Any = schema
        for part in parts:
            assert isinstance(target, dict), f"Cannot navigate into non-dict at {part}"
            assert part in target, (
                f"Dangling reference: key '{part}' not found in {list(target.keys())}"
            )
            target = target[part]


def test_create_tool_schema_preserves_defs_for_enums_and_models() -> None:
    """AUDIT-13: Preserves $defs and ensures all $ref pointers resolve."""
    schema, args_model = create_tool_schema(tool_with_enum_and_nested_model)
    params = schema.parameters

    assert "$defs" in params
    assert "QueryMode" in params["$defs"]
    assert "FilterConfig" in params["$defs"]

    # Verify Enum definition
    mode_def = params["$defs"]["QueryMode"]
    assert mode_def["enum"] == ["quick", "exhaustive"]
    assert mode_def["type"] == "string"

    # Verify nested BaseModel definition
    config_def = params["$defs"]["FilterConfig"]
    assert config_def["type"] == "object"
    assert "max_results" in config_def["properties"]

    # Verify all references resolve
    _assert_all_refs_resolve(params)

    # Verify model validation still functions
    val = args_model(query="search query", mode=QueryMode.EXHAUSTIVE)
    assert val.model_dump() == {
        "query": "search query",
        "mode": QueryMode.EXHAUSTIVE,
        "filter_config": None,
    }


def test_builtin_tools_schemas_unaffected() -> None:
    """AUDIT-13: Simple builtin tools must not emit unnecessary $defs or extra keys."""
    builtin_tools: list[Callable[..., Any]] = [
        calculator,
        read_file,
        http_fetch,
        sql_readonly,
        web_search,
    ]
    for fn in builtin_tools:
        schema, _ = create_tool_schema(fn)
        assert "$defs" not in schema.parameters
        assert "additionalProperties" not in schema.parameters
        assert set(schema.parameters.keys()) <= {"type", "properties", "required"}
