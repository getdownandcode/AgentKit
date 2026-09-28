"""Google Gemini LLM provider adapter using the official google-genai SDK."""

import uuid
from typing import Any

from google import genai
from google.genai import types

from agentkit.core.errors import AuthenticationError, LLMProviderError
from agentkit.core.types import Role
from agentkit.llm.base import LLMClient, LLMResponse, Message, TokenUsage
from agentkit.tools.models import ToolCall, ToolSchema


class GeminiClient(LLMClient):
    """LLM client adapter for Google Gemini models via google-genai."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        client: Any | None = None,
    ) -> None:
        """Initialize the Gemini client adapter.

        Args:
            api_key: Gemini API key. If omitted, google-genai attempts ADC/env lookup.
            model: Gemini model identifier (default: gemini-2.5-flash).
            client: Optional pre-configured genai.Client instance (useful for mocking).
        """
        self.model = model
        self.api_key = api_key

        if client is not None:
            self._client = client
        elif api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            try:
                self._client = genai.Client()
            except Exception as e:
                raise AuthenticationError(
                    f"Gemini API key not configured and default credentials failed: {e}"
                ) from e

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Execute a conversation turn with Gemini, converting neutral messages and tools.

        Args:
            messages: List of neutral Message instances.
            tools: Optional list of ToolSchema instances for function calling.

        Returns:
            Neutral LLMResponse.
        """
        system_instruction: str | None = None
        contents: list[types.Content] = []

        # Convert messages
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_instruction = msg.content
            elif msg.role == Role.USER:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )
            elif msg.role == Role.ASSISTANT:
                parts: list[types.Part] = []
                if msg.content:
                    parts.append(types.Part.from_text(text=msg.content))
                for tc in msg.tool_calls:
                    parts.append(
                        types.Part.from_function_call(
                            name=tc.name,
                            args=tc.arguments,
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
            elif msg.role == Role.TOOL:
                tool_name = msg.tool_call_id or "tool"
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": msg.content},
                            )
                        ],
                    )
                )

        # Convert tools to Gemini FunctionDeclarations
        config_tools: list[Any] | None = None
        if tools:
            func_decls = [
                types.FunctionDeclaration(
                    name=s.name,
                    description=s.description,
                    parameters_json_schema=s.parameters,
                )
                for s in tools
            ]
            config_tools = [types.Tool(function_declarations=func_decls)]

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=config_tools,
        )

        try:
            raw_response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            err_str = str(exc)
            if "API_KEY_INVALID" in err_str or "unauthenticated" in err_str.lower():
                raise AuthenticationError(f"Gemini API key is invalid: {err_str}") from exc
            status_code = getattr(exc, "code", getattr(exc, "status_code", None))
            raise LLMProviderError(
                provider="gemini",
                message=err_str,
                status_code=int(status_code) if isinstance(status_code, int) else None,
            ) from exc

        # Parse candidate parts
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        if raw_response.candidates:
            candidate = raw_response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "text", None):
                        text_parts.append(part.text)
                    if getattr(part, "function_call", None):
                        fc = part.function_call
                        tool_calls.append(
                            ToolCall(
                                id=str(uuid.uuid4())[:8],
                                name=fc.name,
                                arguments=dict(fc.args or {}),
                            )
                        )

        # Extract usage metrics
        input_tokens = 0
        output_tokens = 0
        if getattr(raw_response, "usage_metadata", None):
            input_tokens = getattr(raw_response.usage_metadata, "prompt_token_count", 0) or 0
            output_tokens = getattr(raw_response.usage_metadata, "candidates_token_count", 0) or 0

        text = "".join(text_parts).strip()
        if not text and getattr(raw_response, "text", None):
            text = raw_response.text

        metadata: dict[str, Any] = {}
        if hasattr(raw_response, "model_dump"):
            try:
                metadata = raw_response.model_dump()
            except Exception:
                metadata = {}

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            raw_metadata=metadata,
        )
