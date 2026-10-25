"""Read-only SQL tool with strict single-SELECT validation and DDL/DML rejection.

Two independent controls keep this tool read-only, because a regex cannot be the only thing
standing between an agent and the application database:

1. **Isolation (primary).** ``get_readonly_engine`` connects with ``DATABASE_READONLY_URL``,
   a dedicated non-superuser role whose ``default_transaction_read_only`` is ``on``. The tool
   refuses to run when that URL is absent and never falls back to the application's own
   ``DATABASE_URL``; each session additionally issues ``SET TRANSACTION READ ONLY`` and
   rejects a superuser connection before executing anything.
2. **Statement validation (defence in depth).** Only a single ``SELECT``/``WITH`` statement is
   accepted, comments are rejected, side-effecting and file-reading built-ins are denied, and
   the output is bounded in SQL so the server cannot compute an unbounded result.

The validation is a lexer over PostgreSQL quoting rules, not a full SQL parser: it masks
string literals (including ``E'...'`` escapes and ``$tag$...$tag$`` dollar quotes) so keywords
and separators inside data never blind the structural scans.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from agentkit.config import get_settings
from agentkit.core.errors import ToolSecurityError
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

#: Built-ins that read server files, run dynamic SQL, mutate sequence state or signal other
#: backends. A plain ``SELECT`` prefix is not enough to make these safe, and the keyword scan
#: cannot see them because they are ordinary function calls.
DISALLOWED_FUNCTIONS = (
    # Server-side filesystem access.
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_ls_logdir",
    "pg_ls_waldir",
    "pg_stat_file",
    "pg_logdir_ls",
    "pg_file_write",
    "pg_file_rename",
    "pg_file_unlink",
    "pg_file_sync",
    "lo_import",
    "lo_export",
    "lo_create",
    "lo_unlink",
    # Dynamic SQL: the payload is a string argument the structural scans never inspect.
    "query_to_xml",
    "query_to_xmlschema",
    "query_to_xml_and_xmlschema",
    "cursor_to_xml",
    "cursor_to_xmlschema",
    # Sequence and server state changes.
    "setval",
    "nextval",
    "pg_logical_emit_message",
    "pg_create_restore_point",
    "pg_switch_wal",
    "pg_promote",
    # Backend control: killing or reloading other sessions is not a read.
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    # Extensions that reach the network or execute commands.
    "dblink",
    "dblink_exec",
    "dblink_connect",
    "dblink_connect_u",
    "dblink_disconnect",
)

_KEYWORD_REGEX = re.compile(
    r"\b(" + "|".join(DISALLOWED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_FUNCTION_REGEX = re.compile(
    r"\b(" + "|".join(DISALLOWED_FUNCTIONS) + r")\s*\(",
    re.IGNORECASE,
)

#: A '#' that is not the start of a JSON path operator (#>, #>>, #-), i.e. a comment marker.
_BARE_HASH_REGEX = re.compile(r"#(?![>~-])")

#: A row-locking clause. LIMIT cannot simply be appended after one of these, so such
#: statements take the subquery-wrap fallback instead.
_LOCKING_CLAUSE_REGEX = re.compile(
    r"\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b",
    re.IGNORECASE,
)

#: A trailing ``LIMIT <count> [OFFSET <n>]`` clause, the only form that bounds the statement's
#: own output. ``ALL`` and ``NULL`` are PostgreSQL's spellings for "no limit".
_TRAILING_LIMIT_REGEX = re.compile(
    r"\blimit\s+(?P<count>all|null|\d+)(?:\s+offset\s+\d+)?\s*$",
    re.IGNORECASE,
)

#: A trailing ``FETCH FIRST|NEXT [<count>] ROWS ONLY|WITH TIES`` clause. A missing count means
#: one row, which is already bounded.
_TRAILING_FETCH_REGEX = re.compile(
    r"\bfetch\s+(?:first|next)\s+(?P<count>\d+)?\s*rows?\s+(?:only|with\s+ties)\s*$",
    re.IGNORECASE,
)

#: Process-wide engine, created on first use. Connection pools are expensive to rebuild and
#: are safe to share, but the engine is still injectable per call so tests and alternative
#: deployments can supply their own without mutating module state.
_readonly_engine: AsyncEngine | None = None


def get_readonly_engine() -> AsyncEngine:
    """Return the shared read-only SQLAlchemy AsyncEngine, creating it on first use.

    Returns:
        An engine bound to ``DATABASE_READONLY_URL``.

    Raises:
        ToolSecurityError: If no dedicated read-only URL is configured. The tool fails closed
            rather than silently running with the application's own (superuser) credentials.
    """
    global _readonly_engine
    if _readonly_engine is None:
        settings = get_settings()
        readonly_url = settings.DATABASE_READONLY_URL
        if not readonly_url:
            logger.error("Refusing to run sql_readonly: DATABASE_READONLY_URL is not configured.")
            raise ToolSecurityError(
                "DATABASE_READONLY_URL is not configured; the read-only SQL tool will not run "
                "against the application's own database role.",
                tool_name="sql_readonly",
            )
        _readonly_engine = create_async_engine(readonly_url, pool_pre_ping=True)
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
            prohibited built-ins, or is not a SELECT/WITH statement.
    """
    stripped = query.strip()
    if not stripped:
        raise ValueError("Empty or whitespace-only SQL query.")

    # Reject comments that could hide payload structure. The scan runs on the query with
    # string literals and quoted identifiers blanked out, so comment markers that are merely
    # data (WHERE note = 'x -- y', or a '#' inside a literal) do not cause false rejections.
    # A marker can only be hidden inside quotes when it really is inside a literal, in which
    # case the database parses it as data too. A bare '#' is allowed when it begins a
    # PostgreSQL JSON path operator (#>, #>>, #-); those are legitimate in a read-only
    # projection.
    masked_structure = _mask_literals(stripped)
    if (
        "--" in masked_structure
        or "/*" in masked_structure
        or "*/" in masked_structure
        or _BARE_HASH_REGEX.search(masked_structure)
    ):
        raise ValueError(
            "Comments are not permitted in SQL queries to prevent security circumvention."
        )

    # Strip a single trailing semicolon if present. rstrip(";") would also swallow the
    # repeated separators that signal a stacked-statement payload. The multi-statement scan
    # likewise runs on the masked structure so a ';' inside a string literal (or inside a
    # dollar-quoted body) is data, not a statement separator; a real separator outside any
    # literal is still caught.
    cleaned = stripped[:-1].strip() if stripped.endswith(";") else stripped
    if ";" in _mask_literals(cleaned):
        raise ValueError("Multiple SQL statements separated by semicolons are strictly prohibited.")

    # Validate statement header: must begin with SELECT or WITH
    lower = cleaned.lower()
    if not (lower.startswith("select") or lower.startswith("with")):
        raise ValueError("Only SELECT statements are permitted.")

    structural = _mask_literals(cleaned)

    # Check for forbidden DDL, DML, or administrative keywords. Scanning the query with its
    # string literals and quoted identifiers blanked out avoids rejecting harmless queries
    # like SELECT * FROM events WHERE action = 'insert', where the keyword only appears as
    # data. The literals are still covered by the single-statement check above, since an
    # embedded ';' inside a literal cannot be resolved without full SQL parsing.
    matches = _KEYWORD_REGEX.findall(structural)
    if matches:
        prohibited = sorted({m.upper() for m in matches})
        raise ValueError(f"Query contains prohibited SQL keyword(s): {', '.join(prohibited)}")

    # Prohibited built-ins are function calls, not keywords, so they need their own scan. The
    # mask means the name only counts when it appears in SQL structure, never inside data.
    function_matches = _FUNCTION_REGEX.findall(structural)
    if function_matches:
        prohibited_functions = sorted({m.lower() for m in function_matches})
        raise ValueError(
            "Query calls prohibited SQL function(s): " + ", ".join(prohibited_functions)
        )

    return cleaned


def _mask_literals(sql: str) -> str:
    """Replace string literals, quoted identifiers and dollar quotes with blanks.

    Keeps every structural scan focused on SQL syntax instead of matching words that merely
    appear inside a quoted value or a quoted column name. The mask preserves string length so
    offsets computed on masked text remain valid for the original statement.

    Three PostgreSQL quoting forms are handled, because each one can otherwise smuggle a
    keyword or a statement separator past a naive scanner:

    - standard ``'...'`` literals with doubled-quote escapes;
    - ``E'...'`` escape strings, where a backslash escapes the next character;
    - ``$tag$...$tag$`` dollar-quoted bodies, where quotes and semicolons are plain data.
    """
    masked: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if char == "$":
            dollar_end = _dollar_quote_end(sql, index)
            if dollar_end is not None:
                masked.append(" " * (dollar_end - index))
                index = dollar_end
                continue

        if char == "'":
            end = _quoted_end(sql, index, "'", backslash_escapes=_has_escape_prefix(sql, index))
            masked.append(" " * (end - index))
            index = end
            continue

        if char == '"':
            end = _quoted_end(sql, index, '"', backslash_escapes=False)
            masked.append(" " * (end - index))
            index = end
            continue

        masked.append(char)
        index += 1

    return "".join(masked)


def _has_escape_prefix(sql: str, quote_index: int) -> bool:
    """Return True when a ``'`` opens a PostgreSQL ``E'...'`` escape string.

    The ``E`` must be a standalone prefix: ``TABLE'x'`` is not one, while ``E'x'`` and
    ``e'x'`` are.
    """
    if quote_index == 0:
        return False
    prefix = sql[quote_index - 1]
    if prefix not in ("E", "e"):
        return False
    if quote_index == 1:
        return True
    before = sql[quote_index - 2]
    return not (before.isalnum() or before in ("_", "$"))


def _quoted_end(sql: str, start: int, quote: str, *, backslash_escapes: bool) -> int:
    """Return the index just past a quoted run starting at ``start``.

    A doubled quote is an escaped quote inside the literal, not its terminator. When
    ``backslash_escapes`` is set (``E'...'``), a backslash escapes the following character.
    An unterminated literal consumes the rest of the statement: it is invalid SQL anyway, and
    treating the tail as data keeps a stray quote from blinding the structural scans.
    """
    index = start + 1
    length = len(sql)
    while index < length:
        char = sql[index]
        if backslash_escapes and char == "\\":
            index += 2
            continue
        if char == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _is_dollar_tag(candidate: str) -> bool:
    """Return True when a run of characters is a valid dollar-quote tag body.

    An empty body is valid and means the plain ``$$`` delimiter form.
    """
    if candidate == "":
        return True
    if not (candidate[0].isalpha() or candidate[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in candidate)


def _dollar_quote_end(sql: str, start: int) -> int | None:
    """Return the index just past a dollar-quoted run, or None if none starts at ``start``.

    ``$1`` and other positional-parameter or operator forms are not dollar quotes: their
    candidate tag is not a valid identifier, so they are left alone.
    """
    tag_end = sql.find("$", start + 1)
    if tag_end == -1:
        return None
    if not _is_dollar_tag(sql[start + 1 : tag_end]):
        return None
    tag = sql[start : tag_end + 1]
    closing = sql.find(tag, tag_end + 1)
    return len(sql) if closing == -1 else closing + len(tag)


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
        engine: Optional custom AsyncEngine override (e.g. for testing). An injected engine is
            caller-owned: the read-only role check applies only to the configured engine.
        row_limit: Maximum number of rows to retrieve.
        timeout_s: Query execution timeout in seconds.

    Returns:
        Formatted markdown table string or 'No rows returned.'

    Raises:
        ValueError: If the statement fails validation.
        ToolSecurityError: If the configured read-only role is missing or privileged.
    """
    clean_sql = validate_sql_query(query)
    owns_engine = engine is None
    target_engine = engine if engine is not None else get_readonly_engine()

    async with asyncio.timeout(timeout_s):
        async with target_engine.connect() as conn:
            await _enforce_read_only_session(conn, strict=owns_engine)
            await _apply_statement_timeout(conn, timeout_s)
            bounded_sql = _apply_row_limit(clean_sql, row_limit)
            cursor = await conn.execute(text(bounded_sql))
            columns = list(cursor.keys())
            rows = cursor.fetchmany(row_limit)
            return format_rows_as_markdown(columns, rows)


async def _enforce_read_only_session(conn: AsyncConnection, *, strict: bool) -> None:
    """Make the session read-only at the database, and reject a privileged role.

    ``SET TRANSACTION READ ONLY`` is belt-and-braces on top of the role's
    ``default_transaction_read_only``: even a misconfigured role cannot write through this
    connection. The role check is the runtime half of the isolation guarantee — a superuser
    URL accidentally configured as ``DATABASE_READONLY_URL`` is refused before any query runs.

    Args:
        conn: Connection about to run the query. Must be in a fresh transaction.
        strict: Whether this is the tool's own configured engine (subject to the role check).

    Raises:
        ToolSecurityError: If a strict connection is a superuser.
    """
    if conn.dialect.name != "postgresql":
        return

    await conn.execute(text("SET TRANSACTION READ ONLY"))
    if not strict:
        return

    is_superuser = (
        await conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"),
        )
    ).scalar_one_or_none()
    if is_superuser is not False:
        logger.error(
            "Refusing to run sql_readonly: connected role is a superuser (rolsuper=%s).",
            is_superuser,
        )
        raise ToolSecurityError(
            "The read-only SQL tool is configured with a superuser role. Point "
            "DATABASE_READONLY_URL at a dedicated non-superuser role.",
            tool_name="sql_readonly",
        )


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
            # MAX_EXECUTION_TIME is in milliseconds and applies session-wide to subsequent
            # top-level SELECT statements, unlike SET STATEMENT ... FOR which would only
            # cover the single statement it prefixes.
            await conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s * 1000)}"))
    except Exception as exc:
        # A restricted role may lack permission to set this; the client-side timeout and the
        # in-SQL row limit still bound the work, so this must not fail the query.
        logger.warning("Could not apply server-side statement timeout: %s", exc)


def _top_level_tail(masked_sql: str) -> str:
    """Return the trailing top-level clause of a masked statement.

    Clause keywords are only meaningful at parenthesis depth zero: a LIMIT inside a subquery
    does not bound the outer statement. Parenthesis counting on the literal-masked text is
    exact for real queries, since quoted parentheses are already blanked out.
    """
    depth = 0
    tail_start = 0
    for index, char in enumerate(masked_sql):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth <= 0:
                depth = 0
                tail_start = index + 1
    return masked_sql[tail_start:]


def _clamp_trailing_bound(sql: str, row_limit: int) -> str | None:
    """Clamp an existing trailing ``LIMIT``/``FETCH FIRST`` clause to ``row_limit``.

    A caller-supplied bound is honoured only when it is *smaller* than the configured maximum.
    ``LIMIT ALL``, ``LIMIT NULL`` and oversized counts otherwise pass the statement through
    unbounded, which is exactly what the cap exists to prevent.

    Args:
        sql: Validated statement.
        row_limit: Configured maximum row count.

    Returns:
        The statement with its bound tightened, the statement unchanged when its own bound is
        already at or below the maximum, or None when it has no trailing bound.
    """
    masked_tail = _top_level_tail(_mask_literals(sql))

    for pattern in (_TRAILING_LIMIT_REGEX, _TRAILING_FETCH_REGEX):
        if pattern.search(masked_tail) is None:
            continue
        # The clause sits at the very end of the statement, so the same characters are
        # present, unmasked, at the end of the original text.
        original = pattern.search(sql)
        if original is None:
            continue
        count = original.group("count")
        if count is None:
            # FETCH FIRST ROWS ONLY means one row: already bounded.
            return sql
        if count.lower() not in ("all", "null") and int(count) <= row_limit:
            return sql
        start, end = original.span("count")
        return f"{sql[:start]}{row_limit}{sql[end:]}"

    return None


def _apply_row_limit(sql: str, row_limit: int) -> str:
    """Bound the statement's output in SQL, preferring an appended LIMIT.

    Appending ``LIMIT`` leaves an already-valid statement untouched in shape, so constructs
    like duplicate output column names in a JOIN keep working exactly as written. The cases
    that need different handling, all detected on the trailing top-level clause, are:

    - the statement already bounds itself (LIMIT / FETCH FIRST): its bound is clamped down to
      the configured maximum, never widened;
    - the statement ends in a row-locking clause (FOR UPDATE / FOR SHARE), after which a
      LIMIT cannot legally appear: wrapped in a limited subquery instead.
    """
    limit = max(1, int(row_limit))
    masked_tail = _top_level_tail(_mask_literals(sql))

    if _LOCKING_CLAUSE_REGEX.search(masked_tail):
        return f"SELECT * FROM ({sql}) AS agentkit_limited_query LIMIT {limit}"

    clamped = _clamp_trailing_bound(sql, limit)
    if clamped is not None:
        return clamped

    return f"{sql} LIMIT {limit}"


@tool
async def sql_readonly(query: str) -> str:
    """Execute a read-only SQL query against the database and return results as text.

    Only single SELECT or WITH statements are allowed. All DDL, DML, multiple statements,
    comment payloads and side-effecting built-ins are strictly blocked. Execution uses the
    dedicated read-only role from ``DATABASE_READONLY_URL``.
    """
    return await execute_sql_query(query)
