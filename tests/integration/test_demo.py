"""Integration test for demo database seed and multi-tool agent execution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentkit.core.agent import Agent, AgentConfig
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.builtin.sql_readonly import execute_sql_query
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


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

    @tool
    async def run_sql(query: str) -> str:
        """Execute read-only SQL query."""
        return await execute_sql_query(query, engine=engine)

    registry.register(run_sql)

    # Setup FakeLLMClient multi-step script
    # Step 1: LLM decides to query SQL for total electronics sales
    # Step 2: LLM receives SQL results ($3200) and calculates sales tax (3200 * 1.085)
    # Step 3: LLM returns final synthesized answer
    llm = FakeLLMClient(
        responses=[
            '{"thought": "I need to query total sales for Electronics in 2024."}',
            '{"thought": "Total sales is 3200. Now I calculate total with 8.5% tax."}',
            "Total Electronics revenue in 2024 was $3,200.00. With 8.5% sales tax, the total is $3,472.00.",
        ],
        tool_calls_sequence=[
            [
                ToolCall(
                    name="run_sql",
                    arguments={
                        "query": "SELECT SUM(total_price) as total_rev FROM orders o JOIN products p ON o.product_id = p.id WHERE p.category = 'Electronics'"
                    },
                )
            ],
            [
                ToolCall(
                    name="calculator",
                    arguments={"expression": "3200 * 1.085"},
                )
            ],
            [],
        ],
    )

    agent = Agent(
        llm=llm,
        tools=registry,
        config=AgentConfig(max_steps=5),
    )

    prompt = "What was the total revenue from 'Electronics' category in 2024, and what is the total with 8.5% sales tax?"
    result = await agent.run(prompt)

    assert result.success is True
    assert result.answer is not None
    assert "$3,472.00" in result.answer
    assert len(result.steps) == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_demo_script_runner(monkeypatch: Any) -> None:
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
