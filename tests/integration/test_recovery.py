from __future__ import annotations

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.types import RunStatus
from agentkit.llm.base import LLMResponse
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.builtin.calculator import calculator
from agentkit.tools.models import ToolCall
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
