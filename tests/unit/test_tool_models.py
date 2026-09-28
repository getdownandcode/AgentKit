"""Unit tests for tool models: ToolResult, ToolCall, and ToolSchema."""

from agentkit.tools.models import ToolCall, ToolResult, ToolSchema


def test_tool_result_success_and_failure() -> None:
    """Verify ToolResult creation, helper methods, and serialization."""
    res_ok = ToolResult.success("Operation completed", latency_ms=42)
    assert res_ok.ok is True
    assert res_ok.output == "Operation completed"
    assert res_ok.error is None
    assert res_ok.truncated is False
    assert res_ok.latency_ms == 42
    assert res_ok.model_dump() == {
        "ok": True,
        "output": "Operation completed",
        "error": None,
        "truncated": False,
        "latency_ms": 42,
    }

    res_err = ToolResult.failure("Invalid input syntax", latency_ms=10)
    assert res_err.ok is False
    assert res_err.output == ""
    assert res_err.error == "Invalid input syntax"
    assert res_err.truncated is False
    assert res_err.latency_ms == 10


def test_tool_call_model() -> None:
    """Verify ToolCall model attributes and serialization."""
    call = ToolCall(id="call_123", name="calculator", arguments={"expression": "2 + 2"})
    assert call.id == "call_123"
    assert call.name == "calculator"
    assert call.arguments == {"expression": "2 + 2"}
    assert call.model_dump() == {
        "id": "call_123",
        "name": "calculator",
        "arguments": {"expression": "2 + 2"},
    }


def test_tool_schema_model() -> None:
    """Verify ToolSchema definition."""
    schema = ToolSchema(
        name="calculator",
        description="Evaluate basic arithmetic",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    )
    assert schema.name == "calculator"
    assert schema.description == "Evaluate basic arithmetic"
    assert "expression" in schema.parameters["properties"]
    assert schema.parameters["required"] == ["expression"]
