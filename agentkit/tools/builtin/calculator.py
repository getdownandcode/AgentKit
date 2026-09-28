"""Safe arithmetic calculator tool powered by strict Python AST parsing."""

import ast

from agentkit.tools.registry import tool

MAX_EXPRESSION_LEN = 1000
MAX_AST_NODES = 100
MAX_EXPONENT = 1000


def _evaluate_node(node: ast.AST) -> int | float:
    """Recursively evaluate an AST node against a strict whitelist of arithmetic operations."""
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)

    if isinstance(node, ast.Constant):
        # Explicitly reject boolean, string, None, complex, etc.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"Disallowed operand type: {type(node.value).__name__}")
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
            if abs(right) > MAX_EXPONENT or (abs(left) > 1000 and right > 100):
                raise ValueError(f"Exponent too large (maximum allowed is {MAX_EXPONENT})")
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

    return _evaluate_node(tree)


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression safely using AST parsing.

    Supported operators: +, -, *, /, //, %, ** and parentheses.
    Zero eval or exec is used.
    """
    result = safe_calculate(expression)
    return str(result)
