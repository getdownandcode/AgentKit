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
    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]

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
