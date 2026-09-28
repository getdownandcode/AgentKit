"""Redis-backed session conversation store with TTL and sliding-window truncation."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from agentkit.core.types import Role
from agentkit.llm.base import Message
from agentkit.memory.base import SessionStore

logger = logging.getLogger(__name__)


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
        """Truncate message history, preserving the leading system prompt and recent messages."""
        if max_messages <= 0 or len(messages) <= max_messages:
            return messages

        if messages and messages[0].role == Role.SYSTEM:
            system_msg = messages[0]
            non_system = messages[1:]
            truncated = non_system[-max_messages:]
            return [system_msg, *truncated]

        return messages[-max_messages:]

    async def get_messages(self, session_id: str) -> list[Message]:
        """Retrieve conversation messages for a session from Redis."""
        key = self._key(session_id)
        data = await self._client.get(key)
        if not data:
            return []

        try:
            raw_list: list[dict[str, Any]] = json.loads(data)
            return [Message.model_validate(item) for item in raw_list]
        except Exception as exc:
            logger.error(
                "Failed to deserialize session messages for session %s: %s",
                session_id,
                exc,
            )
            return []

    async def save_messages(self, session_id: str, messages: list[Message]) -> None:
        """Persist conversation messages in Redis with TTL and sliding window."""
        if self.max_messages is not None:
            messages = self.truncate_messages(messages, self.max_messages)

        key = self._key(session_id)
        serialized = json.dumps([m.model_dump() for m in messages])
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
