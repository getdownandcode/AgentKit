from __future__ import annotations

from pathlib import Path

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.models import ToolCall, ToolResult
from agentkit.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_agent_self_corrects_after_tool_failure() -> None:
    """Verify that an agent recovers when a tool call returns an error and corrects its arguments."""
    registry = ToolRegistry()
    registry.register(calculator)

    fake_llm = FakeLLMClient(
        responses=[
            # Step 1: Model calls calculator with an invalid expression (division by zero)
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call_err_1",
                        name="calculator",
                        arguments={"expression": "100 / 0"},
                    )
                ],
            ),
            # Step 2: Model inspects error, self-corrects to a valid expression
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call_fix_2",
                        name="calculator",
                        arguments={"expression": "100 / 2"},
                    )
                ],
            ),
            # Step 3: Model receives successful result 50 and returns final answer
            LLMResponse(
                text="The corrected calculation result is 50.0.",
                tool_calls=[],
            ),
        ]
    )

    agent = Agent(
        llm=fake_llm,
        registry=registry,
        config=AgentConfig(max_steps=5),
    )

    result = await agent.run(goal="Divide 100 by 2 safely")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The corrected calculation result is 50.0."
    assert result.steps_count == 3

    # Verify conversation history shows the error turn and the recovery turn
    final_history = fake_llm.history[-1]
    tool_turns = [m for m in final_history if m.role.value == "tool"]
    assert len(tool_turns) == 2
    assert (
        "error" in tool_turns[0].content.lower()
        or "division by zero" in tool_turns[0].content.lower()
    )
    assert "50" in tool_turns[1].content


@pytest.mark.asyncio
async def test_agent_recovers_from_file_not_found(tmp_path: Path) -> None:
    """Verify that an agent recovers when reading a non-existent file by retrying with the valid path."""
    sandbox_dir = tmp_path / "sandbox"
    sandbox_dir.mkdir()
    valid_file = sandbox_dir / "target.txt"
    valid_file.write_text("Hello AgentKit!", encoding="utf-8")

    registry = ToolRegistry()

    def safe_read(file_path: str) -> ToolResult:
        from agentkit.tools.builtin.read_file import read_sandboxed_file

        try:
            content = read_sandboxed_file(file_path=file_path, base_dir=sandbox_dir)
            return ToolResult(output=content, ok=True)
        except Exception as exc:
            return ToolResult(output="", ok=False, error=str(exc))

    registry.register(safe_read, name="read_file", description="Read sandboxed file.")

    fake_llm = FakeLLMClient(
        responses=[
            # Step 1: Model requests wrong filename
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call_f1",
                        name="read_file",
                        arguments={"file_path": "wrong.txt"},
                    )
                ],
            ),
            # Step 2: Model handles file not found error and requests correct file
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call_f2",
                        name="read_file",
                        arguments={"file_path": "target.txt"},
                    )
                ],
            ),
            # Step 3: Model receives contents and answers
            LLMResponse(
                text="The target file contains: Hello AgentKit!",
                tool_calls=[],
            ),
        ]
    )

    agent = Agent(
        llm=fake_llm,
        registry=registry,
        config=AgentConfig(max_steps=5),
    )

    result = await agent.run(goal="Read target.txt")
    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The target file contains: Hello AgentKit!"

    tool_turns = [m for m in fake_llm.history[-1] if m.role.value == "tool"]
    assert len(tool_turns) == 2
    assert "not found" in tool_turns[0].content.lower()
    assert "hello agentkit!" in tool_turns[1].content.lower()
