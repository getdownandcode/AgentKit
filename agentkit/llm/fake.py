"""Scripted FakeLLMClient for deterministic unit and integration testing."""

from typing import Any

from agentkit.llm.base import LLMClient, LLMResponse, Message, TokenUsage
from agentkit.tools.models import ToolCall, ToolSchema


class FakeLLMClient(LLMClient):
    """Deterministic LLM client returning scripted responses without making network requests."""

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        default_response: LLMResponse | None = None,
    ) -> None:
        self._responses: list[LLMResponse] = list(responses) if responses else []
        self._default_response: LLMResponse | None = default_response
        self.history: list[list[Message]] = []
        self.tools_history: list[list[ToolSchema] | None] = []
        self.call_count: int = 0

    def add_response(self, response: LLMResponse) -> None:
        """Enqueue an LLMResponse to be yielded on subsequent chat calls."""
        self._responses.append(response)

    def queue_text(
        self,
        text: str,
        usage: TokenUsage | None = None,
    ) -> None:
        """Convenience method to enqueue a simple natural language response."""
        self.add_response(
            LLMResponse(
                text=text,
                usage=usage or TokenUsage(input_tokens=10, output_tokens=10),
            )
        )

    def queue_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str = "call_default",
        text: str = "",
        usage: TokenUsage | None = None,
    ) -> None:
        """Convenience method to enqueue a response requesting a tool call."""
        call = ToolCall(id=call_id, name=tool_name, arguments=arguments)
        self.add_response(
            LLMResponse(
                text=text,
                tool_calls=[call],
                usage=usage or TokenUsage(input_tokens=15, output_tokens=10),
            )
        )

    def reset(self) -> None:
        """Clear recorded call histories and reset counter."""
        self._responses.clear()
        self.history.clear()
        self.tools_history.clear()
        self.call_count = 0

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Record received interaction and return the next scripted response."""
        self.history.append(list(messages))
        self.tools_history.append(list(tools) if tools is not None else None)
        self.call_count += 1

        if self._responses:
            return self._responses.pop(0)

        if self._default_response is not None:
            return self._default_response

        raise RuntimeError(f"FakeLLMClient response queue exhausted on call #{self.call_count}.")
