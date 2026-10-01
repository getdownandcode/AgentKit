"""OpenAI LLM provider adapter using the official openai SDK."""

import json
import logging
import os
import uuid
from typing import Any

import openai

from agentkit.core.errors import AuthenticationError, LLMProviderError, RateLimitExceededError
from agentkit.core.types import Role
from agentkit.llm.base import LLMClient, LLMResponse, Message, TokenUsage
from agentkit.tools.models import ToolCall, ToolSchema

logger = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    """LLM client adapter for OpenAI chat completion models via openai SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        client: Any | None = None,
    ) -> None:
        """Initialize the OpenAI client adapter.

        Args:
            api_key: OpenAI API key. If omitted, uses OPENAI_API_KEY environment variable.
            model: OpenAI model identifier (default: gpt-4o).
            client: Optional pre-configured AsyncOpenAI client instance (useful for mocking).
        """
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        if client is not None:
            self._client = client
        elif self.api_key:
            self._client = openai.AsyncOpenAI(api_key=self.api_key)
        else:
            raise AuthenticationError(
                "OpenAI API key not configured. Set OPENAI_API_KEY environment variable or pass api_key."
            )

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Execute a conversation turn with OpenAI, converting neutral messages and tools.

        Args:
            messages: List of neutral Message instances.
            tools: Optional list of ToolSchema instances for function calling.

        Returns:
            Neutral LLMResponse.
        """
        openai_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                openai_messages.append({"role": "system", "content": msg.content})
            elif msg.role == Role.USER:
                openai_messages.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                assistant_dict: dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    assistant_dict["content"] = msg.content
                if msg.tool_calls:
                    assistant_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                openai_messages.append(assistant_dict)
            elif msg.role == Role.TOOL:
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content,
                    }
                )

        openai_tools: list[dict[str, Any]] | None = None
        if tools:
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        try:
            raw_response = await self._client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as exc:
            raise AuthenticationError(f"OpenAI authentication failed: {exc}") from exc
        except openai.RateLimitError as exc:
            raise RateLimitExceededError(retry_after=60) from exc
        except openai.OpenAIError as exc:
            status_code = getattr(exc, "status_code", None)
            raise LLMProviderError(
                provider="openai",
                message=str(exc),
                status_code=int(status_code) if isinstance(status_code, int) else None,
            ) from exc
        except Exception as exc:
            raise LLMProviderError(provider="openai", message=str(exc)) from exc

        # Response assembly stays inside the error boundary: an empty ``choices`` list would
        # IndexError and unexpected SDK shapes would raise attribute errors. Those are
        # provider faults and must surface as LLMProviderError so callers can map them.
        try:
            if not raw_response.choices:
                raise LLMProviderError(
                    provider="openai",
                    message="Response contained no choices.",
                )

            choice = raw_response.choices[0]
            choice_message = choice.message
            text = choice_message.content or ""
            tool_calls: list[ToolCall] = []

            if getattr(choice_message, "tool_calls", None):
                for tc in choice_message.tool_calls:
                    # Full UUID: tool_call_id is the join key between an assistant tool
                    # call and its tool result, so a truncated 32-bit id risks collisions.
                    call_id = tc.id or str(uuid.uuid4())
                    func_name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except json.JSONDecodeError as exc:
                            # Substituting {"raw": ...} here would surface downstream as a
                            # misleading "missing required field" validation error instead of
                            # naming the real fault: the model emitted malformed JSON.
                            raise LLMProviderError(
                                provider="openai",
                                message=(
                                    f"Tool call '{func_name}' carried malformed JSON arguments: {exc}"
                                ),
                            ) from exc
                    else:
                        parsed_args = dict(raw_args)

                    if not isinstance(parsed_args, dict):
                        raise LLMProviderError(
                            provider="openai",
                            message=(
                                f"Tool call '{func_name}' arguments must decode to an object, "
                                f"got {type(parsed_args).__name__}."
                            ),
                        )

                    tool_calls.append(
                        ToolCall(
                            id=call_id,
                            name=func_name,
                            arguments=parsed_args,
                        )
                    )

            input_tokens = 0
            output_tokens = 0
            if getattr(raw_response, "usage", None):
                input_tokens = getattr(raw_response.usage, "prompt_tokens", 0) or 0
                output_tokens = getattr(raw_response.usage, "completion_tokens", 0) or 0

            metadata: dict[str, Any] = {}
            if hasattr(raw_response, "model_dump"):
                try:
                    metadata = raw_response.model_dump()
                except Exception:
                    metadata = {}
        except (LLMProviderError, AuthenticationError, RateLimitExceededError):
            raise
        except Exception as exc:
            raise LLMProviderError(
                provider="openai",
                message=f"Malformed OpenAI response: {exc}",
            ) from exc

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            raw_metadata=metadata,
        )
