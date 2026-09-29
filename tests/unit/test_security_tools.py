from __future__ import annotations

import pytest

from agentkit.tools.builtin.calculator import safe_calculate
from agentkit.tools.builtin.sql_readonly import validate_sql_query


def test_calculator_ast_evasion_blocked() -> None:
    """Verify that dangerous Python builtins, imports, and arbitrary execution are blocked by AST validator."""
    dangerous_payloads = [
        "__import__('os').system('ls')",
        "__import__('sys').exit(1)",
        "open('/etc/passwd').read()",
        "(1).__class__.__bases__[0].__subclasses__()",
        "eval('1 + 1')",
        "exec('a = 1')",
        "compile('1', '', 'eval')",
        "getattr(int, '__doc__')",
        "lambda: 42",
        "[x for x in range(10)]",
        "{'a': 1}",
        "(lambda x: x)(5)",
        "print('hello')",
        "globals()",
        "locals()",
        "vars()",
        "breakpoint()",
    ]

    for payload in dangerous_payloads:
        with pytest.raises(ValueError) as exc_info:
            safe_calculate(payload)
        msg = str(exc_info.value).lower()
        assert "unsupported" in msg or "disallowed" in msg or "invalid" in msg or "syntax" in msg


def test_calculator_syntax_and_boundary_errors() -> None:
    """Verify malformed and boundary inputs fail cleanly with ValueError."""
    syntax_errors = [
        "",
        "   ",
        "1 + ",
        "+ * 2",
        "((1 + 2)",
        "1 + 2))",
        "2 ** 10000000",  # excessively large exponent power limit
        "invalid_identifier",
        "100 / 0",  # ZeroDivisionError
    ]

    for err in syntax_errors:
        with pytest.raises((ValueError, ZeroDivisionError)):
            safe_calculate(err)


def test_sql_destructive_keywords_blocked() -> None:
    """Verify non-SELECT and destructive statements are strictly rejected."""
    destructive_queries = [
        "DROP TABLE users",
        "DELETE FROM orders WHERE id = 1",
        "UPDATE products SET price = 0",
        "INSERT INTO users (name) VALUES ('hacker')",
        "ALTER TABLE users ADD COLUMN is_admin INT",
        "TRUNCATE TABLE logs",
        "CREATE TABLE backdoor (id INT)",
        "ATTACH DATABASE '/tmp/pwn.db' AS pwn",
        "PRAGMA table_info(users)",
        "VACUUM",
    ]

    for query in destructive_queries:
        with pytest.raises(ValueError) as exc_info:
            validate_sql_query(query=query)
        msg = str(exc_info.value).lower()
        assert "only select statements" in msg or "prohibited" in msg


def test_sql_stacked_queries_and_comments_blocked() -> None:
    """Verify multiple statements and suspicious comment payloads are blocked."""
    stacked_queries = [
        "SELECT 1; DROP TABLE users;",
        "SELECT 1; SELECT 2;",
        "SELECT * FROM products; DELETE FROM products;",
        "SELECT 1 /* comment */",
        "SELECT 1 -- comment",
    ]

    for query in stacked_queries:
        with pytest.raises(ValueError) as exc_info:
            validate_sql_query(query=query)
        msg = str(exc_info.value).lower()
        assert "multiple" in msg or "comment" in msg or "prohibited" in msg
