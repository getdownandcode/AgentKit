"""End-to-End Multi-Tool Demo for AgentKit.

Demonstrates an autonomous ReAct agent resolving a multi-step analytical question
by combining the read-only SQL tool with the safe AST calculator.

Problem:
    "What was the total revenue from 'Electronics' category in 2024, and what would
    be the total if we applied an 8.5% sales tax?"

By default the demo runs against the infrastructure configured in the environment
(DATABASE_URL / REDIS_URL), so the run and step records it produces are persisted to
PostgreSQL and Redis exactly as they would be in production. The seed script creates
``products`` and ``orders`` tables in the target database.

Pass ``--offline`` for a fully hermetic run that needs no services and no API keys.

Usage:
    docker compose up -d postgres redis
    python examples/demo.py --offline
    python examples/demo.py --provider gemini
    python examples/demo.py --provider openai
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agentkit.config import get_settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.pg_trace import PostgresTraceSink
from agentkit.core.trace import InMemoryTraceSink, TraceSink
from agentkit.llm.base import LLMClient
from agentkit.llm.factory import create_llm_client
from agentkit.llm.fake import FakeLLMClient
from agentkit.memory.base import InMemoryMemoryStore, MemoryStore, SessionStore
from agentkit.memory.pg_store import PostgresMemoryStore
from agentkit.memory.redis_store import RedisMemoryStore
from agentkit.memory.tiered import TieredMemoryStore
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.sql_readonly import execute_sql_query
from agentkit.tools.registry import ToolRegistry

logger = logging.getLogger("agentkit.demo")

IN_MEMORY_DB_URL = "sqlite+aiosqlite:///:memory:"


@dataclass
class DemoResources:
    """Stores and connections owned by a demo run, all closable."""

    memory: MemoryStore
    trace_sink: TraceSink
    session_id: str | None
    redis: Redis | None = None
    engine: AsyncEngine | None = None


async def seed_demo_database(engine: AsyncEngine) -> None:
    """Apply scripts/seed_demo_db.sql, creating products and orders tables."""
    from scripts.seed import seed_database

    await seed_database(engine)


async def build_persistence(db_url: str, offline: bool) -> DemoResources:
    """Select durable or in-memory stores depending on the requested mode."""
    if offline or db_url.startswith("sqlite"):
        logger.info("Using in-memory stores (hermetic mode).")
        return DemoResources(
            memory=InMemoryMemoryStore(),
            trace_sink=InMemoryTraceSink(),
            session_id=None,
        )

    settings = get_settings()
    engine = create_async_engine(db_url, pool_pre_ping=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Run records always go to PostgreSQL; the session tier degrades on its own so a
    # Redis outage costs conversation history rather than the whole demo.
    run_store = PostgresMemoryStore(factory)
    session_store: SessionStore
    redis_client: Redis | None
    try:
        redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        await redis_client.ping()
        session_store = RedisMemoryStore(redis_client, ttl_s=settings.SESSION_TTL_S)
    except Exception as exc:
        logger.warning(
            "Redis unreachable (%s); runs persist to PostgreSQL but sessions stay in memory.",
            exc,
        )
        redis_client = None
        session_store = InMemoryMemoryStore()

    return DemoResources(
        memory=TieredMemoryStore(session_store=session_store, run_store=run_store),
        trace_sink=PostgresTraceSink(factory),
        session_id="demo-session" if redis_client is not None else None,
        redis=redis_client,
        engine=engine,
    )


def build_fake_llm() -> LLMClient:
    """Create FakeLLMClient for deterministic offline execution."""
    client = FakeLLMClient()
    client.queue_tool_call(
        tool_name="sql_readonly",
        arguments={
            "query": (
                "SELECT SUM(o.total_price) AS total_revenue "
                "FROM orders o JOIN products p ON o.product_id = p.id "
                "WHERE p.category = 'Electronics' AND o.order_date >= '2024-01-01' "
                "AND o.order_date <= '2024-12-31'"
            )
        },
        call_id="call_sql_1",
        text='{"thought": "I should query the database for 2024 orders of Electronics products."}',
    )
    client.queue_tool_call(
        tool_name="calculator",
        arguments={"expression": "3200 * 1.085"},
        call_id="call_calc_1",
        text='{"thought": "SQL query returned $3200 total revenue. Now calculate 8.5% sales tax (3200 * 1.085)."}',
    )
    client.queue_text(
        "The total revenue from the 'Electronics' category in 2024 was $3,200.00. "
        "With an 8.5% sales tax applied, the total comes to $3,472.00."
    )
    return client


def build_llm(offline: bool, provider: str) -> LLMClient:
    """Resolve the LLM client for the demo.

    Offline execution has to be requested with ``--offline``. A missing API key is otherwise
    an error rather than a silent downgrade to the scripted client, which would otherwise
    print a canned answer that looks like a real run.
    """
    if offline or provider == "fake":
        return build_fake_llm()

    settings = get_settings()
    key_present = settings.GEMINI_API_KEY if provider == "gemini" else settings.OPENAI_API_KEY
    if not key_present:
        raise SystemExit(
            f"No API key configured for provider '{provider}'. "
            f"Set {'GEMINI_API_KEY' if provider == 'gemini' else 'OPENAI_API_KEY'}, "
            "or pass --offline to run against the scripted client."
        )

    return create_llm_client(provider)


async def run_demo(
    offline: bool = False,
    provider: str = "gemini",
    db_url: str | None = None,
) -> str:
    """Run the multi-tool AgentKit demonstration.

    Args:
        offline: Use FakeLLMClient and in-memory stores with no external services.
        provider: LLM provider name ('gemini', 'openai', 'fake') when not offline.
        db_url: SQLAlchemy URL override. Defaults to the configured DATABASE_URL,
            falling back to in-memory SQLite when offline.

    Returns:
        The agent's final answer string.
    """
    # Settings are only read on the live path. Offline mode deliberately avoids
    # get_settings() so the demo runs with no API keys and no services at all.
    target_db_url = IN_MEMORY_DB_URL if offline else (db_url or get_settings().DATABASE_URL)

    query_engine = create_async_engine(target_db_url, pool_pre_ping=True)
    await seed_demo_database(query_engine)

    resources = await build_persistence(target_db_url, offline)

    registry = ToolRegistry()
    registry.register(calculator)

    async def execute_demo_sql(query: str) -> str:
        """Execute a read-only SQL query against the demo database."""
        return await execute_sql_query(query, engine=query_engine)

    registry.register(
        execute_demo_sql,
        name="sql_readonly",
        description="Execute a read-only SQL query and return the results as a markdown table.",
    )

    llm = build_llm(offline, provider)
    agent = Agent(
        llm=llm,
        registry=registry,
        memory=resources.memory,
        trace=resources.trace_sink,
        config=AgentConfig(
            max_steps=6,
            run_timeout_s=30.0,
            system_prompt=(
                "You are an expert data analyst assistant. Answer user questions "
                "accurately using available tools (sql_readonly and calculator)."
            ),
        ),
    )

    question = (
        "What was the total revenue from 'Electronics' category in 2024, "
        "and what would be the total if we applied an 8.5% sales tax?"
    )
    logger.info("Executing AgentKit with prompt: %s", question)

    try:
        result = await agent.run(question, session_id=resources.session_id)
        logger.info(
            "Agent run finished. Status: %s, Steps: %d",
            result.status.value,
            result.steps_count,
        )
        logger.info("Run ID: %s", result.run_id)
        logger.info("Final Answer:\n%s", result.final_answer)
        return result.final_answer or ""
    finally:
        await query_engine.dispose()
        if resources.engine is not None:
            await resources.engine.dispose()
        if resources.redis is not None:
            aclose = getattr(resources.redis, "aclose", None)
            if callable(aclose):
                await aclose()
            else:
                await resources.redis.close()


def main() -> None:
    """CLI entrypoint for running the demo."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="AgentKit Multi-Tool ReAct Demo")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run hermetically with FakeLLMClient, in-memory SQLite and no services",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="gemini",
        choices=["gemini", "openai", "fake"],
        help="LLM provider to use (default: gemini)",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="SQLAlchemy database URL (defaults to DATABASE_URL, or in-memory SQLite when offline)",
    )
    args = parser.parse_args()

    answer = asyncio.run(run_demo(offline=args.offline, provider=args.provider, db_url=args.db_url))
    print("\n" + "=" * 60)
    print("AGENTKIT DEMO COMPLETED")
    print("=" * 60)
    print(answer)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
