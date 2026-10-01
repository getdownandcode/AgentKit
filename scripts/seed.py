"""Database seeding script for AgentKit demo tables."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agentkit.config import get_settings

logger = logging.getLogger("agentkit.seed")


def split_sql_statements(sql_content: str) -> list[str]:
    """Split a SQL script into individual statements.

    ``str.split(";")`` is wrong for anything but the simplest script: a semicolon inside a
    string literal, a dollar-quoted body or a line comment would split a statement in half
    and leave a fragment that fails to parse.

    Args:
        sql_content: Full text of a SQL script.

    Returns:
        Non-empty, stripped statement strings in source order.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(sql_content)

    while index < length:
        char = sql_content[index]

        if char == "-" and sql_content.startswith("--", index):
            end = sql_content.find("\n", index)
            index = length if end == -1 else end
            continue

        if char == "/" and sql_content.startswith("/*", index):
            end = sql_content.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue

        if char == "'":
            closing = _scan_quoted(sql_content, index, "'")
            current.append(sql_content[index:closing])
            index = closing
            continue

        if char == '"':
            closing = _scan_quoted(sql_content, index, '"')
            current.append(sql_content[index:closing])
            index = closing
            continue

        if char == "$":
            tag_end = sql_content.find("$", index + 1)
            if tag_end != -1 and _is_dollar_tag(sql_content[index + 1 : tag_end]):
                tag = sql_content[index : tag_end + 1]
                closing = sql_content.find(tag, tag_end + 1)
                end = length if closing == -1 else closing + len(tag)
                current.append(sql_content[index:end])
                index = end
                continue

        if char == ";":
            statements.append("".join(current).strip())
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    statements.append("".join(current).strip())
    return [stmt for stmt in statements if stmt]


def _is_dollar_tag(candidate: str) -> bool:
    """Return True when a run of characters is a valid dollar-quote tag body.

    An empty body is valid and means the plain ``$$`` delimiter form.
    """
    return candidate == "" or candidate.replace("_", "").isalnum()


def _scan_quoted(sql_content: str, start: int, quote: str) -> int:
    """Return the index just past a quoted run starting at ``start``.

    A doubled quote character is an escaped quote inside the literal, not its terminator.
    """
    index = start + 1
    length = len(sql_content)
    while index < length:
        if sql_content[index] == quote:
            if index + 1 < length and sql_content[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return length


async def seed_database(engine: AsyncEngine, script_path: Path | None = None) -> None:
    """Execute demo SQL seed script against the provided database engine.

    Args:
        engine: Connected AsyncEngine instance.
        script_path: Optional custom path to SQL seed file (defaults to scripts/seed_demo_db.sql).
    """
    if script_path is None:
        script_path = Path(__file__).parent / "seed_demo_db.sql"

    if not script_path.exists():
        raise FileNotFoundError(f"Seed script not found at {script_path}")

    sql_content = script_path.read_text(encoding="utf-8")
    statements = split_sql_statements(sql_content)

    logger.info("Applying %d SQL statements from %s", len(statements), script_path.name)
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))

    logger.info("Successfully seeded demo database tables (products, orders).")


async def main() -> None:
    """CLI entrypoint for standalone database seeding."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Seed demo products and orders database tables.")
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Target database URL (defaults to DATABASE_URL in environment/settings).",
    )
    args = parser.parse_args()

    db_url = args.db_url or get_settings().DATABASE_URL
    engine = create_async_engine(db_url, pool_pre_ping=True)
    try:
        await seed_database(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
