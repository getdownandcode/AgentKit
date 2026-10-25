"""Regression tests for SQL tool isolation and the row cap (AUDIT-01 / AUDIT-02).

The validator is defence in depth, not the primary control: these tests cover the statement
scan, and ``tests/real/test_sql_isolation_real.py`` covers the dedicated-role enforcement that
only a live PostgreSQL can prove.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from agentkit.core.errors import ToolSecurityError
from agentkit.tools.builtin import sql_readonly as sql_tool
from agentkit.tools.builtin.sql_readonly import (
    _apply_row_limit,
    execute_sql_query,
    get_readonly_engine,
    set_readonly_engine,
    validate_sql_query,
)


@pytest.mark.parametrize(
    "payload",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('/etc/passwd')",
        "SELECT setval('runs_id_seq', 1)",
        "SELECT nextval('runs_id_seq')",
        "SELECT pg_terminate_backend(12345)",
        "SELECT pg_cancel_backend(12345)",
        "SELECT query_to_xml('SELECT * FROM runs', true, true, '')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(1, '/tmp/leak')",
        "SELECT dblink_exec('dbname=other', 'DROP TABLE runs')",
        "SELECT pg_logical_emit_message(true, 'prefix', 'payload')",
        "SELECT * FROM t WHERE pg_sleep IS NULL AND pg_reload_conf()",
    ],
)
def test_side_effecting_and_file_reading_functions_are_rejected(payload: str) -> None:
    """A SELECT prefix does not make a privileged built-in read-only."""
    with pytest.raises(ValueError, match="prohibited SQL function"):
        validate_sql_query(payload)


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        # A dollar-quoted body hides the quote that used to blind the keyword scan.
        ("SELECT $$'$$ INTO x", "prohibited SQL keyword"),
        # An E'' escape string hides the quote that used to blind the comment scan.
        ("SELECT E'\\''; DROP TABLE runs; --'", "Comments are not permitted"),
    ],
)
def test_dollar_quote_and_escape_string_payloads_are_rejected(
    payload: str, expected_error: str
) -> None:
    """Both quoting forms must be understood by the structural scans."""
    with pytest.raises(ValueError, match=expected_error):
        validate_sql_query(payload)


@pytest.mark.parametrize(
    "payload",
    [
        "SELECT $$it's fine$$ AS tag",
        "SELECT $body$ a ; b -- c $body$ AS body",
        "SELECT E'line\\nbreak' AS escaped",
        "SELECT * FROM events WHERE action = 'insert'",
        "SELECT 'drop me' AS harmless",
    ],
)
def test_legitimate_literals_still_pass(payload: str) -> None:
    """Masking must not turn ordinary data into a false positive."""
    assert validate_sql_query(payload) == payload


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SELECT * FROM runs LIMIT ALL", "SELECT * FROM runs LIMIT 50"),
        ("SELECT * FROM runs LIMIT NULL", "SELECT * FROM runs LIMIT 50"),
        ("SELECT * FROM runs LIMIT 1000000000", "SELECT * FROM runs LIMIT 50"),
        # A caller-bound smaller than the cap is honoured as written.
        ("SELECT * FROM runs LIMIT 5", "SELECT * FROM runs LIMIT 5"),
        ("SELECT * FROM runs LIMIT 5 OFFSET 100", "SELECT * FROM runs LIMIT 5 OFFSET 100"),
        # A LIMIT inside a subquery does not bound the outer statement.
        (
            "SELECT * FROM (SELECT 1 LIMIT 1000) AS inner_q",
            "SELECT * FROM (SELECT 1 LIMIT 1000) AS inner_q LIMIT 50",
        ),
        # FETCH FIRST is the SQL-standard spelling of the same bound.
        (
            "SELECT * FROM runs FETCH FIRST 1000 ROWS ONLY",
            "SELECT * FROM runs FETCH FIRST 50 ROWS ONLY",
        ),
        ("SELECT * FROM runs FETCH FIRST ROWS ONLY", "SELECT * FROM runs FETCH FIRST ROWS ONLY"),
        # The server-side cap is applied even to statements that were already limited.
        ("SELECT * FROM runs LIMIT ALL", "SELECT * FROM runs LIMIT 50"),
    ],
)
def test_row_cap_clamps_existing_bounds(statement: str, expected: str) -> None:
    """No wording of LIMIT may widen the configured maximum."""
    assert _apply_row_limit(statement, 50) == expected


def test_row_cap_wraps_locking_clauses() -> None:
    """LIMIT cannot follow FOR UPDATE, so the statement is wrapped instead."""
    wrapped = _apply_row_limit("SELECT * FROM runs FOR UPDATE", 50)
    assert wrapped.endswith("LIMIT 50")
    assert "agentkit_limited_query" in wrapped


@pytest.mark.parametrize("statement", ["SELECT * FROM runs LIMIT ALL", "SELECT * FROM runs"])
@pytest.mark.asyncio
async def test_executed_statement_is_bounded_in_sql(statement: str) -> None:
    """The cap must reach the database, not only ``fetchmany``."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    executed: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(
        _conn: Any,
        _cursor: Any,
        statement_sql: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        executed.append(statement_sql)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE runs (id INTEGER PRIMARY KEY)"))

    await execute_sql_query(statement, engine=engine, row_limit=7)
    await engine.dispose()

    selects = [sql for sql in executed if sql.strip().upper().startswith("SELECT")]
    assert selects, "the query never reached the database"
    assert "LIMIT 7" in selects[-1].upper()


def _settings(**overrides: Any) -> SimpleNamespace:
    """Minimal stand-in for Settings with only the URLs this module reads."""
    values: dict[str, Any] = {
        "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/agentkit",
        "DATABASE_READONLY_URL": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _reset_shared_engine() -> Iterator[None]:
    """Keep the module-level engine from leaking between tests."""
    set_readonly_engine(None)
    yield None
    set_readonly_engine(None)


def test_missing_readonly_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a dedicated URL the tool must refuse, never borrow the app credentials."""
    created: list[Any] = []

    def _record(*args: Any, **_kwargs: Any) -> Any:
        created.append(args)
        raise AssertionError("no engine may be created without DATABASE_READONLY_URL")

    monkeypatch.setattr(sql_tool, "get_settings", lambda: _settings())
    monkeypatch.setattr(sql_tool, "create_async_engine", _record)

    with pytest.raises(ToolSecurityError, match="DATABASE_READONLY_URL"):
        get_readonly_engine()
    assert created == []


def test_readonly_engine_never_uses_the_application_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured read-only URL is used verbatim, and DATABASE_URL is never a fallback."""
    captured: list[str] = []

    class _StubEngine:
        """Enough of AsyncEngine for the factory to return it."""

    def _capture(url: str, **_kwargs: Any) -> Any:
        captured.append(url)
        return _StubEngine()

    readonly_url = "postgresql+asyncpg://agentkit_ro:secret@db:5432/agentkit"
    monkeypatch.setattr(
        sql_tool,
        "get_settings",
        lambda: _settings(DATABASE_READONLY_URL=readonly_url),
    )
    monkeypatch.setattr(sql_tool, "create_async_engine", _capture)

    engine = get_readonly_engine()

    assert captured == [readonly_url]
    assert engine is get_readonly_engine()
    assert captured == [readonly_url], "the engine must be created once and reused"
