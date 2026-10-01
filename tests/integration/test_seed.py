"""Integration tests for demo database seeding script and idempotency."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.seed import seed_database


@pytest.mark.asyncio
async def test_seed_database_idempotent() -> None:
    """Verify seed_database creates tables and rows, and second run does not fail."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # First run
    await seed_database(engine)

    async with engine.connect() as conn:
        res_prod = await conn.execute(text("SELECT COUNT(*) FROM products"))
        count_prod = res_prod.scalar()
        assert count_prod == 7

        res_orders = await conn.execute(text("SELECT COUNT(*) FROM orders"))
        count_orders = res_orders.scalar()
        assert count_orders == 5

    # Second run should succeed cleanly with ON CONFLICT DO NOTHING
    await seed_database(engine)

    async with engine.connect() as conn:
        res_prod2 = await conn.execute(text("SELECT COUNT(*) FROM products"))
        assert res_prod2.scalar() == 7

        res_orders2 = await conn.execute(text("SELECT COUNT(*) FROM orders"))
        assert res_orders2.scalar() == 5

    await engine.dispose()
