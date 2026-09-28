"""Unit and security tests for the safe AST calculator tool."""

import pytest

from agentkit.tools.builtin.calculator import calculator, safe_calculate
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


def test_basic_arithmetic() -> None:
    assert safe_calculate("2 + 2") == 4
    assert safe_calculate("10 - 3") == 7
    assert safe_calculate("4 * 5") == 20
    assert safe_calculate("15 / 3") == 5.0
    assert safe_calculate("17 // 3") == 5
    assert safe_calculate("17 % 3") == 2
    assert safe_calculate("2 ** 4") == 16


def test_precedence_and_grouping() -> None:
    assert safe_calculate("2 + 3 * 4") == 14
    assert safe_calculate("(2 + 3) * 4") == 20
    assert safe_calculate("((10 - 2) * (3 + 1)) / 4") == 8.0


def test_unary_operations() -> None:
    assert safe_calculate("-5 + 10") == 5
    assert safe_calculate("+7 * -2") == -14
    assert safe_calculate("-(-5)") == 5


def test_float_calculations() -> None:
    assert safe_calculate("3.5 + 2.5") == 6.0
    assert safe_calculate("1.5 * 3") == 4.5


def test_division_by_zero() -> None:
    with pytest.raises(ValueError, match="[Dd]ivision by zero"):
        safe_calculate("10 / 0")

    with pytest.raises(ValueError, match="[Dd]ivision by zero|modulo by zero"):
        safe_calculate("10 // 0")

    with pytest.raises(ValueError, match="(?i)modulo by zero"):
        safe_calculate("10 % 0")


def test_empty_or_whitespace_expression() -> None:
    with pytest.raises(ValueError, match="Empty"):
        safe_calculate("")

    with pytest.raises(ValueError, match="Empty"):
        safe_calculate("   ")


def test_syntax_errors() -> None:
    with pytest.raises(ValueError, match="Invalid expression syntax"):
        safe_calculate("2 +* 3")

    with pytest.raises(ValueError, match="Invalid expression syntax"):
        safe_calculate("((2 + 3)")


@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('echo hacked')",
        "eval('2 + 2')",
        "exec('x = 1')",
        "open('/etc/passwd')",
        "print('hello')",
        "abs(-5)",
        "x + 1",
        "True + 1",
        "False * 10",
        "'hello' + 'world'",
        "[1, 2, 3][0]",
        "(1).__class__.__bases__",
        "lambda x: x + 1",
        "{'a': 1}['a']",
    ],
)
def test_malicious_payloads_strictly_rejected(payload: str) -> None:
    with pytest.raises(ValueError):
        safe_calculate(payload)


def test_dos_protection_large_exponents() -> None:
    with pytest.raises(ValueError, match="Exponent too large"):
        safe_calculate("2 ** 100000")

    with pytest.raises(ValueError, match="Exponent too large"):
        safe_calculate("10 ** 10000")


def test_calculator_tool_function() -> None:
    assert calculator("10 + 20") == "30"
    assert calculator("7 / 2") == "3.5"


@pytest.mark.asyncio
async def test_calculator_registry_integration() -> None:
    registry = ToolRegistry()
    registry.register(calculator)

    call = ToolCall(id="call_1", name="calculator", arguments={"expression": "12 * 12"})
    result = await registry.execute(call)
    assert result.ok is True
    assert result.output == "144"

    bad_call = ToolCall(id="call_2", name="calculator", arguments={"expression": "10 / 0"})
    bad_result = await registry.execute(bad_call)
    assert bad_result.ok is False
    assert "division by zero" in (bad_result.error or "").lower()
