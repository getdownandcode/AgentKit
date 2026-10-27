"""Safe arithmetic calculator tool powered by strict Python AST parsing."""

import ast
import math

from agentkit.tools.registry import tool

MAX_EXPRESSION_LEN = 1000
MAX_AST_NODES = 100
MAX_EXPONENT = 1000
#: When the base magnitude grows too, an exponent well below MAX_EXPONENT can already
#: overflow a float (``1000.0 ** 100`` is ~1e300; ``1999.0 ** 101`` is inf), so pairs of an
#: oversized base and a still-large exponent are rejected before the operation runs.
MAX_POW_BASE_MAGNITUDE = 1000
MAX_POW_PAIR_EXPONENT = 100

#: Rough ceiling on the bit length of an exponentiation result. 10**1000 is ~3322 bits and
#: still cheap once, but the ReAct loop can repeat the call, so the result size itself is
#: capped rather than only the exponent.
MAX_RESULT_BITS = 4096


def _evaluate_node(node: ast.AST) -> int | float:
    """Recursively evaluate an AST node against a strict whitelist of arithmetic operations."""
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)

    if isinstance(node, ast.Constant):
        # Explicitly reject boolean, string, None, complex, etc.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"Disallowed operand type: {type(node.value).__name__}")
        # A float literal that overflows Python's double range (e.g. ``1e999``) silently
        # becomes inf at parse time; anchoring finiteness here keeps it out of every
        # subsequent operation.
        if isinstance(node.value, float) and not math.isfinite(node.value):
            raise ValueError("Numeric literal is not finite (out of range)")
        return node.value

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)

        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("Division by zero")
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ValueError("Division by zero")
            return left // right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise ValueError("Modulo by zero")
            return left % right
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT or (
                abs(left) > MAX_POW_BASE_MAGNITUDE and right > MAX_POW_PAIR_EXPONENT
            ):
                raise ValueError(f"Exponent too large (maximum allowed is {MAX_EXPONENT})")

            # The exponent bound alone still permits enormous big integers (10**1000 is a
            # 1001-digit result), and the agent loop can repeat the call. Bound the size of
            # the result itself so a single expression cannot consume noticeable CPU.
            # Only integer bases can grow; float exponentiation always yields a float.
            if (
                isinstance(left, int)
                and isinstance(right, int)
                and abs(left) > 1
                and abs(right) * left.bit_length() > MAX_RESULT_BITS
            ):
                raise ValueError(
                    f"Result too large to compute safely (exceeds {MAX_RESULT_BITS} bits)."
                )
            return left**right

        raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

    raise ValueError(f"Unsupported expression construct: {type(node).__name__}")


def safe_calculate(expression: str) -> int | float:
    """Safely parse and evaluate an arithmetic string without using eval or exec.

    Args:
        expression: Mathematical expression string (e.g., "(2 + 3) * 4").

    Returns:
        The calculated numerical result (int or float).

    Raises:
        ValueError: If expression is empty, malformed, too complex, or uses disallowed operations.
    """
    cleaned = expression.strip()
    if not cleaned:
        raise ValueError("Empty or whitespace-only expression.")

    if len(cleaned) > MAX_EXPRESSION_LEN:
        raise ValueError(f"Expression exceeds maximum length of {MAX_EXPRESSION_LEN} characters.")

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc

    # Prevent deeply nested or combinatorial DoS attacks
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError(f"Expression too complex (exceeds {MAX_AST_NODES} AST nodes).")

    result = _evaluate_node(tree)

    # Any float operand is finite, but sums/products/divisions can still overflow into
    # inf or collapse into nan. Intercept once at the exit so neither ever reaches the
    # prompt or history. (The only finite value derivable from an inf intermediate is
    # inf ** 0 == 1.0, which is the correct x**0 answer rather than a masked overflow.)
    if isinstance(result, float) and not math.isfinite(result):
        raise ValueError("Result is not a finite number")

    return result


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression safely using AST parsing.

    Supported operators: +, -, *, /, //, %, ** and parentheses.
    Zero eval or exec is used.
    """
    result = safe_calculate(expression)
    return str(result)
