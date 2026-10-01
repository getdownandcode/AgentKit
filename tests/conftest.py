"""Shared pytest fixtures.

Most of the suite is hermetic: SQLite, fakeredis and FakeLLMClient stand in for
PostgreSQL, Redis and the LLM providers. That keeps `pytest` fast and free of
external dependencies, but it means those paths never exercise the real drivers.

The fixtures here back a `real_infra` marked suite that talks to actual
PostgreSQL and Redis instances. They are skipped unless explicitly enabled with
AGENTKIT_REAL_INFRA=1 so the default `pytest` invocation stays hermetic.

    AGENTKIT_REAL_INFRA=1 pytest -m real_infra

Connection URLs are taken from DATABASE_TEST_URL / REDIS_TEST_URL, falling back
to the application's DATABASE_URL / REDIS_URL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agentkit.config import get_settings

REAL_INFRA_ENV_FLAG = "AGENTKIT_REAL_INFRA"
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

#: Only AgentKit-owned key prefixes are removed between tests so pointing the
#: suite at a shared development Redis cannot destroy unrelated data.
MANAGED_REDIS_PATTERNS = ("session:*", "ratelimit:*")


def real_infra_enabled() -> bool:
    """Return True when the real-infrastructure suite has been opted into."""
    return os.getenv(REAL_INFRA_ENV_FLAG, "").strip().lower() in TRUTHY_VALUES


def _skip_real_infra() -> None:
    """Skip the calling test unless real infrastructure is enabled."""
    pytest.skip(
        f"Real infrastructure suite disabled. Set {REAL_INFRA_ENV_FLAG}=1 to run against "
        "live PostgreSQL and Redis."
    )


def database_test_url() -> str:
    """Resolve the PostgreSQL URL used by the real-infrastructure suite."""
    return (
        os.getenv("DATABASE_TEST_URL") or os.getenv("DATABASE_URL") or get_settings().DATABASE_URL
    )


def redis_test_url() -> str:
    """Resolve the Redis URL used by the real-infrastructure suite."""
    return os.getenv("REDIS_TEST_URL") or os.getenv("REDIS_URL") or get_settings().REDIS_URL


def _upgrade_schema(database_url: str) -> None:
    """Apply Alembic migrations against the target database.

    Deliberately drives the real Alembic entrypoint rather than
    ``Base.metadata.create_all`` so the migration scripts themselves are covered.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    try:
        command.upgrade(cfg, "head")
    except Exception as exc:
        pytest.fail(
            f"Could not apply migrations to {database_url!r}. Is PostgreSQL reachable and "
            f"does the database exist? Underlying error: {exc}"
        )


@pytest.fixture(scope="session")
def real_database_url() -> Iterator[str]:
    """Session-scoped PostgreSQL URL with migrations applied once."""
    if not real_infra_enabled():
        _skip_real_infra()

    url = database_test_url()
    if "+asyncpg" not in url:
        pytest.fail(
            f"Real infrastructure tests require an asyncpg DATABASE_TEST_URL, got: {url!r}. "
            "SQLite cannot validate PostgreSQL-specific behaviour."
        )

    _upgrade_schema(url)
    yield url


@pytest.fixture
async def real_engine(real_database_url: str) -> AsyncIterator[AsyncEngine]:
    """Async engine bound to live PostgreSQL, truncated before each test."""
    engine = create_async_engine(real_database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE steps, runs RESTART IDENTITY CASCADE"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def real_session_factory(
    real_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory bound to live PostgreSQL."""
    yield async_sessionmaker(bind=real_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def real_redis() -> AsyncIterator[Redis[str]]:
    """Live Redis client with AgentKit-owned keys cleared before each test."""
    if not real_infra_enabled():
        _skip_real_infra()

    client: Redis[str] = Redis.from_url(redis_test_url(), decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment problem, not a code path
        pytest.fail(f"Could not reach Redis at {redis_test_url()!r}: {exc}")

    await _purge_managed_keys(client)
    try:
        yield client
    finally:
        await _purge_managed_keys(client)
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            await aclose()
        else:
            await client.close()


async def _purge_managed_keys(client: Redis[str]) -> None:
    """Delete only the key prefixes AgentKit owns."""
    for pattern in MANAGED_REDIS_PATTERNS:
        async for key in client.scan_iter(match=pattern, count=200):
            await client.delete(key)
