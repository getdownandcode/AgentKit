"""Unit tests for the core ReAct reasoning step loop."""

import pytest

from agentkit.core.agent import Agent, AgentConfig
from agentkit.core.types import Role, RunStatus
from agentkit.llm.base import LLMResponse, TokenUsage
from agentkit.llm.fake import FakeLLMClient
from agentkit.tools.models import ToolCall
from agentkit.tools.registry import ToolRegistry, tool


@pytest.fixture
def agent_with_tools() -> tuple[Agent, FakeLLMClient, ToolRegistry]:
    registry = ToolRegistry()

    @tool(registry=registry)
    def calculator(expression: str) -> str:
        """Evaluate math."""
        if expression == "2 + 2":
            return "4"
        if expression == "4 * 10":
            return "40"
        return "0"

    @tool(registry=registry)
    def get_user_name(user_id: int) -> str:
        """Fetch user name."""
        return "Alice" if user_id == 1 else "Unknown"

    llm = FakeLLMClient()
    agent = Agent(llm=llm, registry=registry, config=AgentConfig(max_steps=5))
    return agent, llm, registry


@pytest.mark.asyncio
async def test_react_loop_direct_answer(
    agent_with_tools: tuple[Agent, FakeLLMClient, ToolRegistry],
) -> None:
    """Verify single-turn loop where model answers directly without tools."""
    agent, llm, _ = agent_with_tools
    llm.queue_text("Direct answer: The sky is blue.")

    result = await agent.run(goal="What color is the sky?")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "Direct answer: The sky is blue."
    assert result.steps_count == 1
    assert llm.call_count == 1
    assert result.total_input_tokens == 10
    assert result.total_output_tokens == 10


@pytest.mark.asyncio
async def test_react_loop_single_tool_call_and_answer(
    agent_with_tools: tuple[Agent, FakeLLMClient, ToolRegistry],
) -> None:
    """Verify two-step ReAct loop: tool call -> tool result -> final answer."""
    agent, llm, _ = agent_with_tools

    # Step 1: Model requests tool call
    call = ToolCall(id="c_1", name="calculator", arguments={"expression": "2 + 2"})
    llm.add_response(
        LLMResponse(
            text="Let me calculate that.",
            tool_calls=[call],
            usage=TokenUsage(input_tokens=20, output_tokens=15),
        )
    )

    # Step 2: Model gives final answer based on result
    llm.add_response(
        LLMResponse(
            text="The answer is 4.",
            usage=TokenUsage(input_tokens=35, output_tokens=10),
        )
    )

    result = await agent.run(goal="What is 2 + 2?")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "The answer is 4."
    assert result.steps_count == 2
    assert llm.call_count == 2
    assert result.total_input_tokens == 55
    assert result.total_output_tokens == 25

    # Check conversation history in LLM call 2
    history = llm.history[1]
    # system, user, assistant(tool_calls), tool(result)
    assert len(history) == 4
    assert history[0].role == Role.SYSTEM
    assert history[1].role == Role.USER
    assert history[2].role == Role.ASSISTANT
    assert history[3].role == Role.TOOL
    assert history[3].content == "4"


@pytest.mark.asyncio
async def test_react_loop_multi_tool_calls(
    agent_with_tools: tuple[Agent, FakeLLMClient, ToolRegistry],
) -> None:
    """Verify multi-step ReAct loop across multiple distinct tools."""
    agent, llm, _ = agent_with_tools

    # Step 1: lookup user
    llm.queue_tool_call("get_user_name", {"user_id": 1}, call_id="c_u")
    # Step 2: compute user score
    llm.queue_tool_call("calculator", {"expression": "4 * 10"}, call_id="c_c")
    # Step 3: final answer
    llm.queue_text("User Alice has a score of 40.")

    result = await agent.run(goal="Get score for user 1")

    assert result.status == RunStatus.SUCCEEDED
    assert result.final_answer == "User Alice has a score of 40."
    assert result.steps_count == 3
    assert llm.call_count == 3
