"""Unit tests for Agent configuration and dependency injection."""

import pytest

from agentkit.core.agent import Agent, AgentConfig, RunResult
from agentkit.core.types import RunStatus
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.registry import ToolRegistry


def test_agent_config_defaults() -> None:
    """Verify default limits and directives in AgentConfig."""
    cfg = AgentConfig()
    assert cfg.max_steps == 10
    assert cfg.run_timeout_s == 60.0
    assert cfg.tool_timeout_s == 15.0
    assert cfg.tool_output_max_chars == 2000
    assert "tools" in cfg.system_prompt.lower()
    assert "untrusted" in cfg.system_prompt.lower()


def test_agent_initialization() -> None:
    """Verify Agent initialization with injected dependencies."""
    llm = FakeLLMClient()
    registry = ToolRegistry()
    config = AgentConfig(max_steps=5, run_timeout_s=30.0)

    agent = Agent(llm=llm, registry=registry, config=config)
    assert agent.llm is llm
    assert agent.registry is registry
    assert agent.config.max_steps == 5
    assert agent.config.run_timeout_s == 30.0


def test_run_result_model() -> None:
    """Verify RunResult model structure and serialization."""
    result = RunResult(
        run_id="run-12345",
        session_id="session-abc",
        goal="Calculate sum",
        status=RunStatus.SUCCEEDED,
        final_answer="The answer is 42.",
        steps_count=2,
        total_input_tokens=100,
        total_output_tokens=50,
        duration_ms=450,
    )
    assert result.run_id == "run-12345"
    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The answer is 42."
    dump = result.model_dump()
    assert dump["steps_count"] == 2
    assert dump["status"] == "succeeded"
