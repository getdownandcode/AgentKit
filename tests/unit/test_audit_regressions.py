"""Regression tests for the audit hardening pass.

Each test pins behaviour that was previously wrong, so a later refactor cannot silently
reintroduce the bug.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentkit.api.deps import get_llm_client
from agentkit.api.main import create_app
from agentkit.api.ratelimit import RateLimiter
from agentkit.config import Settings
from agentkit.core.errors import AuthenticationError, ServiceUnavailableError
from agentkit.core.trace import StepTrace
from agentkit.llm.base import LLMClient, LLMResponse, Message
from agentkit.llm.retry import RetryingLLMClient
from agentkit.memory.base import InMemoryMemoryStore, RunRecord
from agentkit.memory.tiered import TieredMemoryStore
from agentkit.tools.builtin.calculator import MAX_RESULT_BITS, safe_calculate
from agentkit.tools.builtin.sql_readonly import validate_sql_query


class _State:
    """Stand-in for ``app.state`` exposing only the attributes ``get_llm_client`` reads."""

    def __init__(self, **overrides: Any) -> None:
        self.llm_client: Any = None
        self.settings = Settings(API_KEYS="k", **overrides)


class _Request:
    """Minimal Request stand-in exposing only the ``app.state`` surface deps.py reads.

    Typed as ``Any`` at the call site because ``get_llm_client`` declares a Starlette
    ``Request``, and constructing a real one just to read ``app.state`` adds nothing.
    """

    def __init__(self, state: Any) -> None:
        self.app: Any = type("App", (), {"state": state})()


def _request(state: _State) -> Any:
    """Build the Request stand-in, typed loosely to match the FastAPI dependency signature."""
    return _Request(state)


# --- config: fail closed on API_KEYS -------------------------------------------------


def test_api_keys_must_be_explicit() -> None:
    """A deployment cannot start with a key published in the repository."""
    with pytest.raises(ValidationError, match="API_KEYS must be set"):
        Settings(API_KEYS="")


def test_api_keys_rejects_whitespace_only() -> None:
    with pytest.raises(ValidationError, match="API_KEYS must be set"):
        Settings(API_KEYS="   ")


# --- deps: LLM client fail-closed and retry-wired -------------------------------------


def test_get_llm_client_wraps_provider_with_retry() -> None:
    """The production path must actually use RetryingLLMClient."""
    settings = Settings(API_KEYS="k", LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test")

    with patch(
        "agentkit.api.deps.create_llm_client_from_settings",
        return_value=AsyncMock(spec=LLMClient),
    ) as mock_factory:
        client = get_llm_client(_request(_State()), settings=settings)

    mock_factory.assert_called_once()
    assert isinstance(client, RetryingLLMClient)


def test_get_llm_client_raises_when_provider_unavailable() -> None:
    """Misconfiguration must surface as an error, not as a scripted 200 response."""
    settings = Settings(API_KEYS="k", LLM_PROVIDER="gemini")

    with (
        patch(
            "agentkit.api.deps.create_llm_client_from_settings",
            side_effect=AuthenticationError("no key"),
        ),
        pytest.raises(ServiceUnavailableError) as excinfo,
    ):
        get_llm_client(_request(_State()), settings=settings)

    assert excinfo.value.service == "llm"


def test_get_llm_client_honours_explicit_override() -> None:
    """An app.state override wins and is not wrapped, so tests stay deterministic."""
    settings = Settings(API_KEYS="k", LLM_PROVIDER="gemini")
    state = _State()
    sentinel = AsyncMock(spec=LLMClient)
    state.llm_client = sentinel

    assert get_llm_client(_request(state), settings=settings) is sentinel


def test_get_llm_client_fake_provider_short_circuits() -> None:
    settings = Settings(API_KEYS="k", LLM_PROVIDER="fake")

    with patch("agentkit.api.deps.create_llm_client_from_settings") as mock_factory:
        client = get_llm_client(_request(_State()), settings=settings)

    mock_factory.assert_not_called()
    assert isinstance(client, LLMClient)


# --- deps: the agent must not be pinned to the first request's dependencies -----------


class _ProbeLLM(LLMClient):
    """LLM stub that identifies itself, so a stale-agent bug is visible in the answer."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    async def chat(
        self,
        messages: list[Message],  # noqa: ARG002 - LLMClient signature
        tools: list[Any] | None = None,  # noqa: ARG002 - LLMClient signature
    ) -> LLMResponse:
        return LLMResponse(text=f"answer from {self.tag}")


async def test_agent_reflects_dependency_changes_between_requests() -> None:
    """Rebuilding per request means a later LLM override is not served by a stale agent.

    The old implementation memoized the first assembled Agent on app.state, so the second
    request reused the first request's LLM client regardless of any override.
    """
    from agentkit.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = create_app(settings=Settings(API_KEYS="secret", LLM_PROVIDER="fake"))
    app.state.session_factory = factory
    app.state.llm_client = _ProbeLLM("first")
    # No context manager: that would run the lifespan, which opens a real Redis pool.
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Key": "secret"}

    resp = client.post("/runs", json={"goal": "hi"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"] == "answer from first"

    app.state.llm_client = _ProbeLLM("second")
    resp = client.post("/runs", json={"goal": "hi"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"] == "answer from second"

    await engine.dispose()


# --- tiered: trace forwarding ----------------------------------------------------------


async def test_tiered_forwards_get_run_trace() -> None:
    """TieredMemoryStore must expose the durable steps, not silently return empty."""
    rows = [
        StepTrace(
            run_id="r1",
            step_no=1,
            tool_name="calculator",
            args={"expression": "2+2"},
            result={"output": "4"},
        )
    ]

    class _RunStore:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_run_trace(self, run_id: str) -> list[StepTrace]:
            self.calls.append(run_id)
            return rows

    run_store = _RunStore()
    tiered = TieredMemoryStore(session_store=InMemoryMemoryStore(), run_store=run_store)

    traces = await tiered.get_run_trace("r1")

    assert traces == rows
    assert run_store.calls == ["r1"]


async def test_tiered_forwards_list_runs() -> None:
    class _RunStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str | None, int]] = []

        async def list_runs(self, session_id: str | None = None, limit: int = 50) -> list[Any]:
            self.calls.append((session_id, limit))
            return [RunRecord(id="r1", goal="g")]

    run_store = _RunStore()
    tiered = TieredMemoryStore(session_store=InMemoryMemoryStore(), run_store=run_store)

    runs = await tiered.list_runs(session_id="s1", limit=5)

    assert len(runs) == 1
    assert run_store.calls == [("s1", 5)]


async def test_tiered_trace_helpers_tolerate_bare_run_store() -> None:
    """A run store without trace support yields empty lists rather than raising."""

    class _Bare:
        pass

    tiered = TieredMemoryStore(session_store=InMemoryMemoryStore(), run_store=_Bare())

    assert await tiered.get_run_trace("r1") == []
    assert await tiered.list_runs() == []


# --- retry: no __getattr__ recursion ----------------------------------------------------


def test_retrying_client_getattr_on_uninitialized_instance() -> None:
    """A partially constructed wrapper must raise AttributeError, not recurse."""
    wrapper = RetryingLLMClient.__new__(RetryingLLMClient)

    with pytest.raises(AttributeError):
        _ = wrapper.anything


def test_retrying_client_delegates_attributes() -> None:
    inner = AsyncMock(spec=LLMClient)
    inner.some_marker = "visible"
    wrapper = RetryingLLMClient(inner)

    assert wrapper.some_marker == "visible"


# --- health endpoint: no infrastructure detail leakage ----------------------------------


def test_health_does_not_leak_connection_error_details() -> None:
    """str(exc) from a driver can embed host, port and database name."""

    class _Engine:
        @asynccontextmanager
        async def connect(self) -> AsyncIterator[Any]:
            raise RuntimeError(
                'connection to server at "db.internal.corp:5432", database agentkit failed'
            )
            yield  # pragma: no cover - unreachable, keeps this an async context manager

    app = create_app(settings=Settings(API_KEYS="k"))
    app.state.db_engine = _Engine()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")

    assert resp.status_code == 503
    body = resp.text
    assert "db.internal.corp" not in body
    assert "5432" not in body
    assert "Database connectivity check failed." in body


# --- sql_readonly: literal-aware keyword scan --------------------------------------------


def test_keyword_inside_string_literal_is_allowed() -> None:
    query = "SELECT * FROM audit_events WHERE action = 'insert'"
    assert validate_sql_query(query) == query


def test_comment_markers_inside_literals_are_data_not_comments() -> None:
    """Masking applies to the comment and separator scans, not just the keyword scan."""
    assert (
        validate_sql_query("SELECT * FROM t WHERE note = 'x -- y'")
        == "SELECT * FROM t WHERE note = 'x -- y'"
    )
    assert validate_sql_query("SELECT * FROM t WHERE x = '#5'") == "SELECT * FROM t WHERE x = '#5'"
    assert (
        validate_sql_query("SELECT 'it''s; still one' FROM t") == "SELECT 'it''s; still one' FROM t"
    )


def test_real_comment_after_literal_still_rejected() -> None:
    """An empty literal must not let a following comment hide behind the mask."""
    with pytest.raises(ValueError, match="Comments are not permitted"):
        validate_sql_query("SELECT * FROM t WHERE a = '' -- '")
    with pytest.raises(ValueError, match="Comments are not permitted"):
        validate_sql_query("SELECT 1 -- comment")


def test_keyword_outside_literal_is_still_blocked() -> None:
    with pytest.raises(ValueError, match="prohibited SQL keyword"):
        validate_sql_query("SELECT 'drop me' FROM logs UNION SELECT grant FROM users")


def test_keyword_inside_identifier_not_split_on_underscore() -> None:
    """A column literally named delete_flag is data, not a write statement."""
    assert validate_sql_query("SELECT delete_flag FROM events") == "SELECT delete_flag FROM events"


def test_write_keyword_beside_literal_still_blocked() -> None:
    """Masking literals must not let a real keyword in the surrounding SQL slip through."""
    with pytest.raises(ValueError, match="prohibited SQL keyword"):
        validate_sql_query("SELECT 'safe text' FROM t WHERE grant = 1")


def test_quoted_identifier_containing_keyword_allowed() -> None:
    query = 'SELECT "update_count" FROM metrics'
    assert validate_sql_query(query) == query


def test_hash_json_operator_is_not_treated_as_comment() -> None:
    """'#>' is a legitimate PostgreSQL JSON path operator, not a comment."""
    query = "SELECT payload #>> '{user,name}' FROM events"
    assert validate_sql_query(query) == query


def test_bare_hash_comment_still_rejected() -> None:
    with pytest.raises(ValueError, match="Comments are not permitted"):
        validate_sql_query("SELECT 1 # sneaky comment")


def test_trailing_semicolon_stripped_once() -> None:
    assert validate_sql_query("SELECT 1;") == "SELECT 1"
    with pytest.raises(ValueError, match="Multiple SQL statements"):
        validate_sql_query("SELECT 1;;")


# --- llm adapters: parsing failures must use the domain error taxonomy -------------------


class _Msg:
    def __init__(self, content: str | None = None, tool_calls: Any = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Msg) -> None:
        self.message = message


class _Usage:
    prompt_tokens = 3
    completion_tokens = 4


class _RawResponse:
    def __init__(self, choices: Any, usage: Any = None) -> None:
        self.choices = choices
        self.usage = usage


def _openai_client(choices: Any) -> Any:
    from agentkit.llm.openai import OpenAIClient

    sdk_client = AsyncMock()
    sdk_client.chat.completions.create = AsyncMock(return_value=_RawResponse(choices, _Usage()))
    return OpenAIClient(api_key="sk-test", client=sdk_client)


def _tool_call(name: str, arguments: str, call_id: str = "call_1") -> Any:
    func = AsyncMock()
    func.name = name
    func.arguments = arguments
    tc = AsyncMock()
    tc.id = call_id
    tc.function = func
    return tc


async def test_openai_malformed_tool_arguments_raise_provider_error() -> None:
    """Malformed JSON must name the real fault, not fail later as a missing field."""
    from agentkit.core.errors import LLMProviderError as _ProviderError

    client = _openai_client([_Choice(_Msg(tool_calls=[_tool_call("calc", "{not json")]))])

    with pytest.raises(_ProviderError, match="malformed JSON arguments"):
        await client.chat(messages=[])


async def test_openai_non_object_tool_arguments_raise_provider_error() -> None:
    from agentkit.core.errors import LLMProviderError as _ProviderError

    client = _openai_client([_Choice(_Msg(tool_calls=[_tool_call("calc", "[1, 2, 3]")]))])

    with pytest.raises(_ProviderError, match="must decode to an object"):
        await client.chat(messages=[])


async def test_openai_empty_choices_raise_provider_error() -> None:
    """An empty choices list must surface as LLMProviderError, not an IndexError."""
    from agentkit.core.errors import LLMProviderError as _ProviderError

    client = _openai_client([])

    with pytest.raises(_ProviderError, match="no choices"):
        await client.chat(messages=[])


async def test_openai_tool_call_ids_are_full_uuids() -> None:
    """A truncated 32-bit tool_call_id risks collisions within a long session."""
    client = _openai_client(
        [_Choice(_Msg(tool_calls=[_tool_call("calc", "{}", call_id="")]))],
    )

    response = await client.chat(messages=[])

    call_id = response.tool_calls[0].id
    assert len(call_id) == 36, f"expected a full UUID, got {call_id!r}"


async def test_gemini_tool_call_ids_are_full_uuids() -> None:
    from agentkit.llm.gemini import GeminiClient

    function_call = SimpleNamespace(name="calc", args={"expression": "2+2"})
    part = SimpleNamespace(text=None, function_call=function_call, thought_signature=None)
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[part]))
    raw = SimpleNamespace(candidates=[candidate], usage_metadata=None, text="")

    sdk_client = AsyncMock()
    sdk_client.aio.models.generate_content = AsyncMock(return_value=raw)

    client = GeminiClient(api_key="key", client=sdk_client)
    response = await client.chat(messages=[])

    call_id = response.tool_calls[0].id
    assert len(call_id) == 36, f"expected a full UUID, got {call_id!r}"


async def test_gemini_malformed_response_raises_provider_error() -> None:
    """Blocked prompts raise on attribute access; that must map to LLMProviderError."""
    from agentkit.core.errors import LLMProviderError as _ProviderError
    from agentkit.llm.gemini import GeminiClient

    class _Exploding:
        @property
        def candidates(self) -> Any:
            raise ValueError("prompt was blocked")

    raw = _Exploding()
    sdk_client = AsyncMock()
    sdk_client.aio.models.generate_content = AsyncMock(return_value=raw)

    client = GeminiClient(api_key="key", client=sdk_client)

    with pytest.raises(_ProviderError, match="Malformed Gemini response"):
        await client.chat(messages=[])


# --- sql_readonly: server-side row limit ---------------------------------------------------


async def test_sql_query_is_limited_in_sql_not_just_client_side() -> None:
    """fetchmany alone still makes the database run the full scan."""
    from sqlalchemy import text

    from agentkit.tools.builtin.sql_readonly import execute_sql_query

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        await conn.execute(
            text("INSERT INTO t (id, v) VALUES (1, 'a'), (2, 'b'), (3, 'c')"),
        )

    output = await execute_sql_query("SELECT v FROM t", engine=engine, row_limit=2)

    lines = [line for line in output.strip().split("\n") if line.strip()]
    assert lines == ["| v |", "| --- |", "| a |", "| b |"]
    await engine.dispose()


def test_row_limit_rewrite_prefers_appending_over_wrapping() -> None:
    """Appending LIMIT keeps duplicate-column JOINs working; wrapping is the fallback.

    Verified against PostgreSQL 16, which accepts both forms: the append form is preferred
    because it cannot change the shape of an already-valid statement.
    """
    from agentkit.tools.builtin.sql_readonly import _apply_row_limit

    assert _apply_row_limit("SELECT o.id, p.id FROM o JOIN p ON true", 50).endswith("LIMIT 50")
    assert "agentkit_limited_query" not in _apply_row_limit("SELECT 1", 50)
    assert _apply_row_limit("SELECT * FROM t LIMIT 5", 50) == "SELECT * FROM t LIMIT 5"
    wrapped = _apply_row_limit("SELECT * FROM t FOR UPDATE", 50)
    assert "agentkit_limited_query" in wrapped and wrapped.endswith("LIMIT 50")


# --- seed script: statement-aware splitting -------------------------------------------------


def test_seed_splitter_respects_literals_and_dollar_quotes() -> None:
    """A naive split(';') would tear these statements in half."""
    import sys

    sys.path.insert(0, ".")
    from scripts.seed import split_sql_statements

    sql = (
        "INSERT INTO t (v) VALUES ('semi; colon');\n"
        "-- a comment; with a semicolon\n"
        "SELECT * FROM t WHERE v = 'it''s fine';\n"
        "CREATE FUNCTION f() RETURNS int AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;\n"
    )

    statements = split_sql_statements(sql)

    assert len(statements) == 3, statements
    assert statements[0] == "INSERT INTO t (v) VALUES ('semi; colon')"
    assert statements[1] == "SELECT * FROM t WHERE v = 'it''s fine'"
    assert "BEGIN RETURN 1; END;" in statements[2]


def test_seed_splitter_ignores_trailing_content() -> None:
    import sys

    sys.path.insert(0, ".")
    from scripts.seed import split_sql_statements

    assert split_sql_statements("SELECT 1;\n\n") == ["SELECT 1"]
    assert split_sql_statements("") == []


# --- calculator: bounded result size ------------------------------------------------------


def test_exponentiation_result_size_is_bounded() -> None:
    """The per-exponent guard cannot catch a nested form that balloons the result.

    ``(999**100)**5`` keeps every individual exponent inside MAX_EXPONENT and under the
    existing large-base guard, but the inner power is already ~997 bits and raising it to the
    fifth produces roughly 5000. The result size check is what rejects it.
    """
    two_power = safe_calculate("2**1000")
    assert isinstance(two_power, int)
    assert two_power.bit_length() <= MAX_RESULT_BITS

    assert safe_calculate("10**1000") == 10**1000

    with pytest.raises(ValueError, match="too large to compute safely"):
        safe_calculate("(999**100)**5")


# --- ratelimit: the non-scripting fallback stays atomic -------------------------------------


@pytest.mark.asyncio
async def test_unatomic_fallback_does_not_over_admit_under_concurrency() -> None:
    """WATCH/MULTI must keep a parallel burst from exceeding the quota.

    The previous two-phase trim/count/insert let every concurrent request observe the same
    pre-insert cardinality and be admitted together.
    """
    client: Redis[Any] = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = RateLimiter(requests_per_window=5, window_seconds=60)

    results = await asyncio.gather(
        *(
            limiter._check_window_unatomic(client, "ratelimit:k", 1000.0, 60.0, 5, f"m{i}", 65)
            for i in range(20)
        ),
        return_exceptions=True,
    )

    admitted = [r for r in results if r == (True, 0)]
    denied = [r for r in results if isinstance(r, tuple) and r[0] is False]
    errors = [r for r in results if isinstance(r, Exception)]

    assert not errors, f"unexpected exceptions: {errors}"
    assert len(admitted) == 5, f"expected exactly 5 admissions, got {len(admitted)}"
    assert len(denied) == 15
