"""End-to-End Multi-Tool Demo for AgentKit.

Demonstrates an autonomous ReAct agent resolving a multi-step analytical question
by combining the read-only SQL tool with the safe AST calculator.

Problem:
    "What was the total revenue from 'Electronics' category in 2024, and what would
    be the total if we applied an 8.5% sales tax?"

Usage:
    python examples/demo.py --offline
    python examples/demo.py --provider gemini
    python examples/demo.py --provider openai
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agentkit.config import get_settings
from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.trace import InMemoryTraceSink
from agentkit.llm.base import LLMClient
from agentkit.llm.factory import create_llm_client
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.sql_readonly import execute_sql_query
from agentkit.tools.registry import ToolRegistry

logger = logging.getLogger("agentkit.demo")


async def setup_demo_database(db_url: str) -> AsyncEngine:
    """Initialize and seed demo database from scripts/seed_demo_db.sql."""
    engine = create_async_engine(db_url)
    seed_path = Path(__file__).parents[1] / "scripts" / "seed_demo_db.sql"

    if not seed_path.exists():
        raise FileNotFoundError(f"Seed script not found at {seed_path}")

    sql_content = seed_path.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]

    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))

    logger.info("Demo database seeded successfully with products and orders.")
    return engine


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


async def run_demo(
    offline: bool = False,
    provider: str = "gemini",
    db_url: str | None = None,
) -> str:
    """Run the multi-tool AgentKit demonstration.

    Args:
        offline: If True, uses FakeLLMClient with scripted ReAct turns.
        provider: Provider name ('gemini', 'openai', 'fake') if offline=False.
        db_url: Optional SQLAlchemy database URL override.

    Returns:
        The agent's final answer string.
    """
    settings = get_settings()
    target_db_url = db_url or "sqlite+aiosqlite:///:memory:"

    # 1. Initialize and seed database
    engine = await setup_demo_database(target_db_url)

    # 2. Register tools in an isolated registry
    registry = ToolRegistry()
    registry.register(calculator)

    async def execute_demo_sql(query: str) -> str:
        """Execute a read-only SQL query against the database and return results as markdown table."""
        return await execute_sql_query(query, engine=engine)

    registry.register(
        execute_demo_sql,
        name="sql_readonly",
        description="Execute a read-only SQL query against the database and return results as markdown table.",
    )

    # 3. Choose LLM Client
    llm: LLMClient
    if offline or provider == "fake":
        llm = build_fake_llm()
    else:
        # Check for provider API key
        if provider == "gemini" and not settings.GEMINI_API_KEY:
            logger.warning("No GEMINI_API_KEY detected. Falling back to offline FakeLLMClient.")
            llm = build_fake_llm()
        elif provider == "openai" and not settings.OPENAI_API_KEY:
            logger.warning("No OPENAI_API_KEY detected. Falling back to offline FakeLLMClient.")
            llm = build_fake_llm()
        else:
            llm = create_llm_client(provider)

    # 4. Create Agent with trace sink
    trace_sink = InMemoryTraceSink()
    agent = Agent(
        llm=llm,
        registry=registry,
        trace=trace_sink,
        config=AgentConfig(
            max_steps=6,
            run_timeout_s=30.0,
            system_prompt=(
                "You are an expert data analyst assistant. Answer user questions "
                "accurately using available tools (sql_readonly and calculator)."
            ),
        ),
    )

    query = (
        "What was the total revenue from 'Electronics' category in 2024, "
        "and what would be the total if we applied an 8.5% sales tax?"
    )

    logger.info("Executing AgentKit with prompt: %s", query)
    result = await agent.run(query)

    logger.info(
        "Agent run finished. Status: %s, Steps: %d",
        result.status.value,
        result.steps_count,
    )
    logger.info("Final Answer:\n%s", result.final_answer)

    await engine.dispose()
    return result.final_answer or ""


def main() -> None:
    """CLI entrypoint for running the demo."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="AgentKit Multi-Tool ReAct Demo")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run completely offline using FakeLLMClient without API keys",
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
        help="Custom SQLAlchemy database URL (defaults to in-memory SQLite)",
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
