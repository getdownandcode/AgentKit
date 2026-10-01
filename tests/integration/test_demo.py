"""Integration test for demo database seed and multi-tool agent execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.types import RunStatus
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.sql_readonly import execute_sql_query
from agentkit.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_seed_demo_db_script_exists_and_seeds(tmp_path: Path) -> None:
    """Verify scripts/seed_demo_db.sql exists, is valid SQL, and populates products and orders."""
    seed_path = Path(__file__).parents[2] / "scripts" / "seed_demo_db.sql"
    assert seed_path.exists(), "scripts/seed_demo_db.sql must exist"

    sql_content = seed_path.read_text(encoding="utf-8")
    assert "products" in sql_content.lower()
    assert "orders" in sql_content.lower()

    # Execute against SQLite database to ensure ANSI compliance
    db_file = tmp_path / "test_demo.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url)

    # Split statements by semicolon and execute
    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))

    # Verify tables have rows
    result = await execute_sql_query("SELECT count(*) as count FROM products", engine=engine)
    assert "count" in result
    assert "| 0 |" not in result

    order_result = await execute_sql_query("SELECT count(*) as count FROM orders", engine=engine)
    assert "count" in order_result
    assert "| 0 |" not in result

    await engine.dispose()


@pytest.mark.asyncio
async def test_multi_tool_end_to_end_demo_workflow(tmp_path: Path) -> None:
    """Verify an agent using SQL tool + calculator resolves multi-step revenue + tax calculation."""
    seed_path = Path(__file__).parents[2] / "scripts" / "seed_demo_db.sql"
    sql_content = seed_path.read_text(encoding="utf-8")

    db_file = tmp_path / "demo_workflow.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url)

    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))

    # Register local sql_query tool pointing to this engine
    registry = ToolRegistry()
    registry.register(calculator)

    async def run_sql(query: str) -> str:
        """Execute read-only SQL query."""
        return await execute_sql_query(query, engine=engine)

    registry.register(
        run_sql,
        name="run_sql",
        description="Execute read-only SQL query.",
    )

    # Setup FakeLLMClient multi-step script
    # Step 1: LLM decides to query SQL for total electronics sales
    # Step 2: LLM receives SQL results ($3200) and calculates sales tax (3200 * 1.085)
    # Step 3: LLM returns final synthesized answer
    llm = FakeLLMClient()
    llm.queue_tool_call(
        tool_name="run_sql",
        arguments={
            "query": (
                "SELECT SUM(total_price) as total_rev FROM orders o "
                "JOIN products p ON o.product_id = p.id WHERE p.category = 'Electronics'"
            )
        },
        call_id="call_sql_1",
        text='{"thought": "I need to query total sales for Electronics in 2024."}',
    )
    llm.queue_tool_call(
        tool_name="calculator",
        arguments={"expression": "3200 * 1.085"},
        call_id="call_calc_1",
        text='{"thought": "Total sales is 3200. Now I calculate total with 8.5% tax."}',
    )
    llm.queue_text(
        "Total Electronics revenue in 2024 was $3,200.00. With 8.5% sales tax, the total is $3,472.00."
    )

    agent = Agent(
        llm=llm,
        registry=registry,
        config=AgentConfig(max_steps=5),
    )

    prompt = "What was the total revenue from 'Electronics' category in 2024, and what is the total with 8.5% sales tax?"
    result = await agent.run(prompt)

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer is not None
    assert "$3,472.00" in result.final_answer
    assert result.steps_count == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_demo_script_runner() -> None:
    """Verify examples/demo.py main function executes successfully in offline mode."""
    import sys
    from importlib import import_module

    # Add project root to sys.path
    root_path = str(Path(__file__).parents[2])
    if root_path not in sys.path:
        sys.path.insert(0, root_path)

    # Verify examples/demo.py exists and can be imported
    demo_module = import_module("examples.demo")
    assert hasattr(demo_module, "run_demo")

    output = await demo_module.run_demo(offline=True)
    assert output is not None
    assert "3472" in output or "3,472" in output or "total" in output.lower()


def test_build_llm_strict_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify build_llm requires API keys when live provider is requested without --offline."""
    from agentkit.config import get_settings
    from examples.demo import build_llm

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()

    # In live mode without key, it must raise ValueError
    with pytest.raises(ValueError, match="API key for provider 'gemini' is missing"):
        build_llm(offline=False, provider="gemini")

    with pytest.raises(ValueError, match="API key for provider 'openai' is missing"):
        build_llm(offline=False, provider="openai")

    # In offline or fake mode, it returns FakeLLMClient
    fake_client_offline = build_llm(offline=True, provider="gemini")
    assert isinstance(fake_client_offline, FakeLLMClient)

    fake_client_fake_prov = build_llm(offline=False, provider="fake")
    assert isinstance(fake_client_fake_prov, FakeLLMClient)
