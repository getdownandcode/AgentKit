"""Google Gemini LLM provider adapter using the official google-genai SDK."""

import re
import uuid
from typing import Any

from google import genai
from google.genai import types

from agentkit.core.errors import AuthenticationError, LLMProviderError
from agentkit.core.types import Role
from agentkit.llm.base import LLMClient, LLMResponse, Message, TokenUsage
from agentkit.tools.models import ToolCall, ToolSchema

#: Candidate finish reasons that mean the model stopped for safety or provenance reasons
#: rather than completing an answer.
_BLOCKED_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}
)

_ENUM_LABEL_REGEX = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _enum_label(value: Any) -> str | None:
    """Return an enum's canonical name, or None when the value is not one.

    SDK enums render as ``FinishReason.SAFETY``; a plain string is accepted too. Anything
    else — a test double's dynamic attribute, a missing field — yields None, so a mock cannot
    be mistaken for a real block reason.
    """
    if value is None:
        return None
    name = getattr(value, "name", None)
    candidate = name if isinstance(name, str) else str(value)
    candidate = candidate.strip().rsplit(".", 1)[-1]
    return candidate if _ENUM_LABEL_REGEX.match(candidate) else None


class GeminiClient(LLMClient):
    """LLM client adapter for Google Gemini models via google-genai."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-3.1-flash-lite",
        client: Any | None = None,
    ) -> None:
        """Initialize the Gemini client adapter.

        Args:
            api_key: Gemini API key. If omitted, google-genai attempts ADC/env lookup.
            model: Gemini model identifier (default: gemini-3.1-flash-lite).
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

        # Map tool_call_id to tool_name for matching function responses
        tool_id_to_name: dict[str, str] = {}
        for msg in messages:
            if msg.role == Role.ASSISTANT:
                for tc in msg.tool_calls:
                    tool_id_to_name[tc.id] = tc.name

        # Convert messages. Consecutive tool results are grouped into a single user turn:
        # Gemini validates that the number of function_response parts equals the number of
        # function_call parts in the turn being answered, so emitting one Content per result
        # makes every parallel tool call fail with 400 INVALID_ARGUMENT.
        index = 0
        while index < len(messages):
            msg = messages[index]
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
                    # The provider's call id is forwarded verbatim so its response can be
                    # correlated; a random uuid would break two parallel calls to one tool.
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                id=tc.id,
                                name=tc.name,
                                args=tc.arguments,
                            ),
                            thought_signature=tc.thought_signature,
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
            elif msg.role == Role.TOOL:
                response_parts: list[types.Part] = []
                while index < len(messages) and messages[index].role == Role.TOOL:
                    tool_msg = messages[index]
                    call_id = tool_msg.tool_call_id
                    tool_name = tool_id_to_name.get(call_id or "") or call_id or "tool"
                    response_parts.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=call_id,
                                name=tool_name,
                                response={"result": tool_msg.content},
                            )
                        )
                    )
                    index += 1
                contents.append(types.Content(role="user", parts=response_parts))
                continue
            index += 1

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

        # Response assembly stays inside the error boundary: reading ``raw_response.text`` raises on
        # blocked prompts, an empty ``candidates`` list would IndexError, and a mock or future
        # SDK shape can drop attributes. All of those are provider faults, so they have to
        # surface as LLMProviderError for callers to map, not as raw SDK exceptions.
        try:
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            saw_thought_part = False
            saw_answer_text = False
            refusal_reason = _gemini_refusal_reason(raw_response)

            candidates = getattr(raw_response, "candidates", None)
            if candidates:
                candidate = candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        # ``thought=True`` marks internal reasoning. It belongs in neither the
                        # answer, the persisted assistant turn, nor the API response. The
                        # check is a strict ``is True`` because test doubles and partial SDK
                        # shapes expose attributes that are truthy without being booleans.
                        if getattr(part, "thought", False) is True:
                            saw_thought_part = True
                        elif getattr(part, "text", None):
                            text_parts.append(part.text)
                            saw_answer_text = True

                        # A thought part may still carry a function call; the call and its
                        # signature must survive even though its text is discarded.
                        if getattr(part, "function_call", None):
                            fc = part.function_call
                            raw_sig = getattr(part, "thought_signature", None)
                            sig = raw_sig if isinstance(raw_sig, bytes) else None
                            provider_id = getattr(fc, "id", None)
                            tool_calls.append(
                                ToolCall(
                                    # The provider's own id is the join key between a call and
                                    # its result, so it is preserved when present. A full UUID
                                    # is the fallback: a truncated 32-bit id would collide
                                    # within a long session.
                                    id=(
                                        provider_id
                                        if isinstance(provider_id, str) and provider_id
                                        else str(uuid.uuid4())
                                    ),
                                    name=fc.name,
                                    arguments=dict(fc.args or {}),
                                    thought_signature=sig,
                                )
                            )

            # Extract usage metrics
            input_tokens = 0
            output_tokens = 0
            if getattr(raw_response, "usage_metadata", None):
                input_tokens = getattr(raw_response.usage_metadata, "prompt_token_count", 0) or 0
                output_tokens = (
                    getattr(raw_response.usage_metadata, "candidates_token_count", 0) or 0
                )

            text = "".join(text_parts).strip()
            # ``raw_response.text`` concatenates every text part, thoughts included, and raises
            # on a blocked prompt. It is therefore only a fallback when the response carried
            # no inspectable parts and was not refused.
            if (
                not text
                and not saw_answer_text
                and not saw_thought_part
                and refusal_reason is None
                and getattr(raw_response, "text", None)
            ):
                text = raw_response.text

            metadata: dict[str, Any] = {}
            if hasattr(raw_response, "model_dump"):
                try:
                    dumped = raw_response.model_dump()
                    if isinstance(dumped, dict):
                        metadata = dumped
                except Exception:
                    metadata = {}
        except (LLMProviderError, AuthenticationError):
            raise
        except Exception as exc:
            raise LLMProviderError(
                provider="gemini",
                message=f"Malformed Gemini response: {exc}",
            ) from exc

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
            refusal_reason=refusal_reason,
            raw_metadata=metadata,
        )


def _gemini_refusal_reason(raw_response: Any) -> str | None:
    """Return why Gemini blocked this response, or None when it did not.

    Two independent places report a block: ``prompt_feedback.block_reason`` for a prompt the
    safety filters rejected outright, and the candidate's ``finish_reason`` for a generation
    stopped mid-way. Neither is an error the SDK raises, so without this check a blocked
    response looks like an empty successful answer.
    """
    feedback = getattr(raw_response, "prompt_feedback", None)
    prompt_block = _enum_label(getattr(feedback, "block_reason", None))
    if prompt_block is not None:
        detail = getattr(feedback, "block_reason_message", None)
        suffix = f": {detail.strip()}" if isinstance(detail, str) and detail.strip() else ""
        return f"Gemini blocked the prompt ({prompt_block}){suffix}."

    candidates = getattr(raw_response, "candidates", None)
    if candidates:
        finish_reason = _enum_label(getattr(candidates[0], "finish_reason", None))
        if finish_reason in _BLOCKED_FINISH_REASONS:
            return f"Gemini stopped the response ({finish_reason})."
    return None
