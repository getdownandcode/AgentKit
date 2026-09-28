"""Unit tests for tool schema generation from callable inspection."""

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
