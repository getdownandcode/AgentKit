"""Unit and security tests for read-only SQL execution tool."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentkit.tools.builtin.sql_readonly import (
    execute_sql_query,
    sql_readonly,
    validate_sql_query,
)
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry


def test_valid_select_queries_pass_validation() -> None:
    assert validate_sql_query("SELECT * FROM users") == "SELECT * FROM users"
    assert (
        validate_sql_query("SELECT id, name FROM products WHERE price > 10;")
        == "SELECT id, name FROM products WHERE price > 10"
    )
    assert validate_sql_query("SELECT count(*) as total FROM orders") == "SELECT count(*) as total FROM orders"


def test_valid_cte_select_passes_validation() -> None:
    query = "WITH recent_orders AS (SELECT id FROM orders) SELECT * FROM recent_orders"
    assert validate_sql_query(query) == query


@pytest.mark.parametrize(
    "comment_payload",
    [
        "SELECT * FROM users -- comment",
        "SELECT * FROM users /* inline comment */",
        "SELECT 1; -- comment",
        "SELECT # comment\n * FROM users",
    ],
)
def test_comments_rejected(comment_payload: str) -> None:
    with pytest.raises(ValueError, match="Comments are not permitted"):
        validate_sql_query(comment_payload)


@pytest.mark.parametrize(
    "multi_statement",
    [
        "SELECT 1; SELECT 2",
        "SELECT * FROM users; DROP TABLE users",
        "SELECT 1 ; UPDATE users SET admin = 1",
    ],
)
def test_multiple_statements_rejected(multi_statement: str) -> None:
    with pytest.raises(ValueError, match="Multiple SQL statements"):
        validate_sql_query(multi_statement)


@pytest.mark.parametrize(
    "forbidden_payload",
    [
        "INSERT INTO users (name) VALUES ('hacker')",
        "UPDATE accounts SET balance = balance + 1000",
        "DELETE FROM logs WHERE 1=1",
        "DROP TABLE users",
        "ALTER TABLE users DROP COLUMN password",
        "CREATE TABLE backdoor (id int)",
        "TRUNCATE TABLE audit_log",
        "SELECT * INTO new_users FROM users",
        "EXEC xp_cmdshell('dir')",
        "EXECUTE immediate 'drop table users'",
        "GRANT ALL PRIVILEGES ON DATABASE test TO public",
        "REVOKE SELECT ON users FROM public",
        "PRAGMA database_list",
    ],
)
def test_write_and_admin_keywords_rejected(forbidden_payload: str) -> None:
    with pytest.raises(ValueError):
        validate_sql_query(forbidden_payload)


def test_non_select_start_rejected() -> None:
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_sql_query("EXPLAIN SELECT 1")

    with pytest.raises(ValueError, match="Empty"):
        validate_sql_query("   ")


@pytest.fixture
async def demo_db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, amount REAL);"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO orders (customer, amount) VALUES ('Alice', 150.0), ('Bob', 85.5), ('Charlie', 220.0);"
            )
        )
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_execute_sql_query_success(demo_db_engine) -> None:
    output = await execute_sql_query("SELECT customer, amount FROM orders ORDER BY id ASC", engine=demo_db_engine)
    assert "| customer | amount |" in output
    assert "Alice" in output
    assert "Bob" in output
    assert "Charlie" in output


@pytest.mark.asyncio
async def test_execute_sql_query_empty_result(demo_db_engine) -> None:
    output = await execute_sql_query("SELECT * FROM orders WHERE amount > 1000", engine=demo_db_engine)
    assert output == "No rows returned."


@pytest.mark.asyncio
async def test_execute_sql_query_row_limit(demo_db_engine) -> None:
    output = await execute_sql_query("SELECT * FROM orders", engine=demo_db_engine, row_limit=2)
    # Header + separator + 2 rows = 4 lines
    lines = [line for line in output.strip().split("\n") if line.strip()]
    assert len(lines) == 4


@pytest.mark.asyncio
async def test_sql_tool_registry_integration(demo_db_engine, monkeypatch) -> None:
    registry = ToolRegistry()
    registry.register(sql_readonly)

    # Patch default engine
    monkeypatch.setattr("agentkit.tools.builtin.sql_readonly.get_readonly_engine", lambda: demo_db_engine)

    call = ToolCall(id="c1", name="sql_readonly", arguments={"query": "SELECT customer FROM orders WHERE id = 1"})
    result = await registry.execute(call)
    assert result.ok is True
    assert "Alice" in result.output

    bad_call = ToolCall(id="c2", name="sql_readonly", arguments={"query": "DELETE FROM orders"})
    bad_result = await registry.execute(bad_call)
    assert bad_result.ok is False
    assert "Only SELECT" in (bad_result.error or "")
