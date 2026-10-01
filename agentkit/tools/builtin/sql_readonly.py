"""Read-only SQL tool with strict single-SELECT validation and DDL/DML rejection."""

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from agentkit.config import get_settings
from agentkit.tools.registry import tool

logger = logging.getLogger(__name__)

DEFAULT_ROW_LIMIT = 50
DEFAULT_STATEMENT_TIMEOUT_S = 10.0

DISALLOWED_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "replace",
    "grant",
    "revoke",
    "exec",
    "execute",
    "into",
    "merge",
    "upsert",
    "attach",
    "detach",
    "pragma",
    "copy",
    "vacuum",
    "call",
    "lock",
    "rename",
)

_KEYWORD_REGEX = re.compile(
    r"\b(" + "|".join(DISALLOWED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

#: A '#' that is not the start of a JSON path operator (#>, #>>, #-), i.e. a comment marker.
_BARE_HASH_REGEX = re.compile(r"#(?![>~-])")

#: A LIMIT or OFFSET clause at the end of the top-level statement. Statements with their own
#: bound are left alone so wrapping never widens a caller-supplied limit.
_TOP_LEVEL_LIMIT_REGEX = re.compile(r"\blimit\s+\d+\s*(?:;)?\s*$", re.IGNORECASE)

#: Process-wide engine, created on first use. Connection pools are expensive to rebuild and
#: are safe to share, but the engine is still injectable per call so tests and alternative
#: deployments can supply their own without mutating module state.
_readonly_engine: AsyncEngine | None = None


def get_readonly_engine() -> AsyncEngine:
    """Return the shared read-only SQLAlchemy AsyncEngine, creating it on first use."""
    global _readonly_engine
    if _readonly_engine is None:
        settings = get_settings()
        _readonly_engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return _readonly_engine


def set_readonly_engine(engine: AsyncEngine | None) -> None:
    """Override or clear the shared read-only engine (dependency injection seam for tests)."""
    global _readonly_engine
    _readonly_engine = engine


def validate_sql_query(query: str) -> str:
    """Validate a SQL query string to ensure it is a safe, single read-only SELECT statement.

    Args:
        query: Candidate SQL statement.

    Returns:
        Cleaned SQL string stripped of trailing semicolon.

    Raises:
        ValueError: If query contains comments, multiple statements, write operations,
            or is not a SELECT/WITH statement.
    """
    stripped = query.strip()
    if not stripped:
        raise ValueError("Empty or whitespace-only SQL query.")

    # Reject comments that could hide payload structure. A bare '#' is allowed when it
    # begins a PostgreSQL JSON path operator (#>, #>>, #-); those are legitimate in a
    # read-only projection, whereas a '#' followed by whitespace opens a MySQL-style comment.
    if (
        "--" in stripped
        or "/*" in stripped
        or "*/" in stripped
        or _BARE_HASH_REGEX.search(stripped)
    ):
        raise ValueError(
            "Comments are not permitted in SQL queries to prevent security circumvention."
        )

    # Strip a single trailing semicolon if present. rstrip(";") would also swallow the
    # repeated separators that signal a stacked-statement payload.
    cleaned = stripped[:-1].strip() if stripped.endswith(";") else stripped
    if ";" in cleaned:
        raise ValueError("Multiple SQL statements separated by semicolons are strictly prohibited.")

    # Validate statement header: must begin with SELECT or WITH
    lower = cleaned.lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        raise ValueError("Only SELECT statements are permitted.")

    # Check for forbidden DDL, DML, or administrative keywords. Scanning the query with its
    # string literals and quoted identifiers blanked out avoids rejecting harmless queries
    # like SELECT * FROM events WHERE action = 'insert', where the keyword only appears as
    # data. The literals are still covered by the single-statement check above, since an
    # embedded ';' inside a literal cannot be resolved without full SQL parsing.
    matches = _KEYWORD_REGEX.findall(_mask_literals(cleaned))
    if matches:
        prohibited = sorted({m.upper() for m in matches})
        raise ValueError(f"Query contains prohibited SQL keyword(s): {', '.join(prohibited)}")

    return cleaned


def _mask_literals(sql: str) -> str:
    """Replace string literals and quoted identifiers with placeholders.

    Keeps the keyword scan focused on SQL structure instead of matching words that merely
    appear inside a quoted value or a quoted column name.
    """
    masked: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if char == "'":
            masked.append(" ")
            index += 1
            while index < length:
                if sql[index] == "'":
                    # Doubled quote is an escaped quote inside the literal, not its end.
                    if index + 1 < length and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        if char == '"':
            masked.append(" ")
            index += 1
            while index < length:
                if sql[index] == '"':
                    if index + 1 < length and sql[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue

        masked.append(char)
        index += 1

    return "".join(masked)


def format_rows_as_markdown(columns: Sequence[str], rows: Sequence[Any]) -> str:
    """Format tabular SQL results into clean Markdown table string."""
    if not rows:
        return "No rows returned."

    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    data_lines = [
        "| " + " | ".join(str(val) if val is not None else "NULL" for val in row) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *data_lines])


async def execute_sql_query(
    query: str,
    engine: AsyncEngine | None = None,
    row_limit: int = DEFAULT_ROW_LIMIT,
    timeout_s: float = DEFAULT_STATEMENT_TIMEOUT_S,
) -> str:
    """Validate and execute a read-only SQL query, returning a formatted markdown table.

    The row limit is applied in SQL, not just client-side. ``fetchmany(row_limit)`` alone still
    makes the database plan and execute the full scan, so a query over a large table burns
    server resources even though only a handful of rows are returned; the client-side
    ``asyncio.timeout`` cancels late and leaves the work already done.

    Args:
        query: SQL SELECT statement.
        engine: Optional custom AsyncEngine override (e.g. for testing).
        row_limit: Maximum number of rows to retrieve.
        timeout_s: Query execution timeout in seconds.

    Returns:
        Formatted markdown table string or 'No rows returned.'
    """
    clean_sql = validate_sql_query(query)
    target_engine = engine or get_readonly_engine()

    async with asyncio.timeout(timeout_s):
        async with target_engine.connect() as conn:
            await _apply_statement_timeout(conn, timeout_s)
            cursor = await conn.execute(text(_apply_row_limit(clean_sql, row_limit)))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(row_limit)
            return format_rows_as_markdown(columns, rows)


async def _apply_statement_timeout(conn: AsyncConnection, timeout_s: float) -> None:
    """Push the timeout down to the database where the dialect supports it.

    The client-side ``asyncio.timeout`` only cancels the wait; the server keeps running the
    statement. ``statement_timeout`` aborts it at the source. SQLite has no equivalent, so the
    client-side timeout remains the only guard there.
    """
    dialect = conn.dialect.name
    if dialect not in ("postgresql", "mysql", "mariadb"):
        return

    try:
        if dialect == "postgresql":
            await conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}"))
        else:
            await conn.execute(
                text(f"SET STATEMENT max_execution_time={int(timeout_s * 1000)} FOR SELECT 1")
            )
    except Exception as exc:
        # A restricted role may lack permission to set this; the client-side timeout and the
        # in-SQL row limit still bound the work, so this must not fail the query.
        logger.warning("Could not apply server-side statement timeout: %s", exc)


def _apply_row_limit(sql: str, row_limit: int) -> str:
    """Wrap the statement in a limited subquery when it carries no top-level LIMIT.

    An existing LIMIT or OFFSET at the end of the statement is left untouched so a caller
    supplied bound is never widened by the wrapper.
    """
    bounded = f"SELECT * FROM ({sql}) AS agentkit_limited_query LIMIT {int(row_limit)}"
    if _TOP_LEVEL_LIMIT_REGEX.search(sql):
        return sql
    return bounded


@tool
async def sql_readonly(query: str) -> str:
    """Execute a read-only SQL query against the database and return results as text.

    Only single SELECT or WITH statements are allowed. All DDL, DML, multiple statements,
    and comment payloads are strictly blocked.
    """
    return await execute_sql_query(query)
