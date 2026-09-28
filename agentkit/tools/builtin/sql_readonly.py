"""Read-only SQL tool with strict single-SELECT validation and DDL/DML rejection."""

import asyncio
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agentkit.config import get_settings
from agentkit.tools.registry import tool

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

_readonly_engine: AsyncEngine | None = None


def get_readonly_engine() -> AsyncEngine:
    """Return singleton read-only SQLAlchemy AsyncEngine."""
    global _readonly_engine
    if _readonly_engine is None:
        settings = get_settings()
        _readonly_engine = create_async_engine(settings.DATABASE_URL)
    return _readonly_engine


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

    # Reject comments that could hide payload structure
    if "--" in stripped or "/*" in stripped or "*/" in stripped or "#" in stripped:
        raise ValueError(
            "Comments are not permitted in SQL queries to prevent security circumvention."
        )

    # Strip single trailing semicolon if present
    cleaned = stripped.rstrip(";").strip()
    if ";" in cleaned:
        raise ValueError("Multiple SQL statements separated by semicolons are strictly prohibited.")

    # Validate statement header: must begin with SELECT or WITH
    lower = cleaned.lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        raise ValueError("Only SELECT statements are permitted.")

    # Check for forbidden DDL, DML, or administrative keywords
    matches = _KEYWORD_REGEX.findall(cleaned)
    if matches:
        prohibited = sorted({m.upper() for m in matches})
        raise ValueError(f"Query contains prohibited SQL keyword(s): {', '.join(prohibited)}")

    return cleaned


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
            cursor = await conn.execute(text(clean_sql))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(row_limit)
            return format_rows_as_markdown(columns, rows)


@tool
async def sql_readonly(query: str) -> str:
    """Execute a read-only SQL query against the database and return results as text.

    Only single SELECT or WITH statements are allowed. All DDL, DML, multiple statements,
    and comment payloads are strictly blocked.
    """
    return await execute_sql_query(query)
