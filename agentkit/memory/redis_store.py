"""Redis-backed session conversation store with TTL and sliding-window truncation."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from redis.asyncio import Redis

from agentkit.core.errors import SessionHistoryError
from agentkit.core.types import Role
from agentkit.llm.base import Message
from agentkit.memory.base import SessionStore
from agentkit.tools.models import THOUGHT_SIGNATURE_BASE64_KEY

logger = logging.getLogger(__name__)


def _encode_message(message: Message) -> dict[str, Any]:
    """Convert a message to a JSON-safe mapping with an explicit binary codec.

    ``model_dump(mode="json")`` cannot be used: Pydantic's default ``ser_json_bytes`` decodes
    ``bytes`` as UTF-8, which raises on a non-UTF-8 Gemini thought signature and silently
    corrupts a UTF-8-decodable one. The signature is therefore tagged and base64-encoded here,
    explicitly, at the persistence boundary.
    """
    payload = message.model_dump(mode="python")
    for call in payload.get("tool_calls") or []:
        signature = call.get("thought_signature")
        if isinstance(signature, bytes):
            call["thought_signature"] = {
                THOUGHT_SIGNATURE_BASE64_KEY: base64.b64encode(signature).decode("ascii")
            }
    return payload


class RedisMemoryStore(SessionStore):
    """Stores conversation message history in Redis with TTL and sliding-window truncation."""

    def __init__(
        self,
        client: Redis[Any],
        ttl_s: int = 3600,
        max_messages: int | None = None,
        key_prefix: str = "session:",
    ) -> None:
        self._client = client
        self.ttl_s = ttl_s
        self.max_messages = max_messages
        self.key_prefix = key_prefix

    def _key(self, session_id: str) -> str:
        """Construct Redis key for session message history."""
        return f"{self.key_prefix}{session_id}:messages"

    @staticmethod
    def truncate_messages(messages: list[Message], max_messages: int) -> list[Message]:
        """Truncate message history to provider-valid turns, keeping the system prompt.

        A plain tail cut can start the kept history on a ``tool`` message whose assistant
        ``tool_calls`` turn was dropped. OpenAI rejects that outright ("messages with role
        'tool' must be a response to a preceding message with 'tool_calls'") and Gemini
        cannot match the response to a call. The cut point is therefore advanced past any
        leading tool results, which are meaningless without the call they answer.

        Args:
            messages: Full conversation history.
            max_messages: Maximum number of messages to return, system prompt included.

        Returns:
            At most ``max_messages`` messages, starting with the system prompt when present
            and never starting with an orphaned tool result.
        """
        if max_messages <= 0 or len(messages) <= max_messages:
            return messages

        system_msg = messages[0] if messages and messages[0].role == Role.SYSTEM else None
        non_system = messages[1:] if system_msg is not None else list(messages)

        # The system prompt occupies one of the max_messages slots, so only max_messages - 1
        # conversational turns fit. Taking a full tail would silently return max_messages + 1
        # entries and grow the history without bound.
        budget = max_messages - (1 if system_msg is not None else 0)
        if budget <= 0:
            return [system_msg] if system_msg is not None else []

        start = max(0, len(non_system) - budget)
        while start < len(non_system) and non_system[start].role == Role.TOOL:
            start += 1

        kept = non_system[start:]
        return ([system_msg] if system_msg is not None else []) + kept

    async def get_messages(self, session_id: str) -> list[Message]:
        """Retrieve conversation messages for a session from Redis.

        Raises:
            SessionHistoryError: If the stored payload cannot be decoded. Returning an empty
                list here is what made corruption indistinguishable from a new conversation:
                the caller would answer without context and then overwrite the stored history.
        """
        key = self._key(session_id)
        data = await self._client.get(key)
        if not data:
            return []

        try:
            raw_list: list[dict[str, Any]] = json.loads(data)
            return [Message.model_validate(item) for item in raw_list]
        except Exception as exc:
            logger.error(
                "Corrupt session history for session %s (%d bytes): %s",
                session_id,
                len(data),
                exc,
            )
            raise SessionHistoryError(
                f"Stored history for session {session_id!r} could not be read; refusing to "
                "start a fresh conversation over it.",
                session_id=session_id,
            ) from exc

    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """Persist conversation messages in Redis with TTL and sliding window."""
        if self.max_messages is not None:
            messages = self.truncate_messages(messages, self.max_messages)

        key = self._key(session_id)
        serialized = json.dumps([_encode_message(m) for m in messages])
        await self._client.set(key, serialized, ex=self.ttl_s)
        logger.debug(
            "Saved %d messages to Redis for session %s (TTL: %ds)",
            len(messages),
            session_id,
            self.ttl_s,
        )

    async def clear_session(self, session_id: str) -> None:
        """Delete conversation history for a session from Redis."""
        key = self._key(session_id)
        await self._client.delete(key)
        logger.debug("Cleared session %s from Redis", session_id)
