# Architecture Decision Log (docs/DECISIONS.md)

This log records significant architectural, technical, and design decisions made throughout the lifecycle of AgentKit.

---

## ADR-001: Framework-less Agent Runtime from Scratch

- **Date**: 2026-09-28
- **Status**: Accepted
- **Context**: AgentKit is designed as a production-grade showcase of core agentic engineering. Many frameworks (LangChain, LlamaIndex, CrewAI) introduce heavy abstractions, opaque control flows, and unnecessary dependencies.
- **Decision**: Build the ReAct reasoning loop, state machine, tool registry, and memory interfaces directly using Python standard libraries and async primitives without third-party agent frameworks.
- **Consequences**:
  - Full control over loop logic, step limits, latency, error recovery, and security checks.
  - Minimal dependency footprint and clean, debuggable call stacks.
  - Requires maintaining our own schema translation and provider integrations.

---

## ADR-002: Provider-Agnostic LLM Adapter Interface

- **Date**: 2026-09-28
- **Status**: Accepted
- **Context**: The agent must support multiple model providers (Google Gemini, OpenAI) interchangeably without modifying core agent logic.
- **Decision**: Define a domain-level `LLMClient` protocol/ABC in `agentkit/llm/base.py` with neutral data models (`Message`, `ToolCall`, `LLMResponse`, `Usage`). Concrete adapters (`GeminiClient`, `OpenAIClient`, `FakeLLMClient`) translate between these neutral types and vendor SDKs.
- **Consequences**:
  - Seamless model swapping via configuration (`LLM_PROVIDER=gemini` or `openai`).
  - Easy testing using `FakeLLMClient` with deterministic scripted responses.
  - Provider-specific quirks (e.g. Gemini FunctionDeclaration vs OpenAI function tool format) are isolated within adapter files.

---

## ADR-003: Tiered Dual-Store Persistence (Redis + PostgreSQL)

- **Date**: 2026-09-28
- **Status**: Accepted
- **Context**: Agent workloads have two conflicting access patterns: fast, short-lived conversational state with automatic expiration, and durable, queryable historical audit logs for compliance and tracing.
- **Decision**:
  - **Redis**: Stores ephemeral conversation messages per `session_id` with a sliding TTL (default 1 hour) and maintains high-throughput sliding window rate-limiting counters.
  - **PostgreSQL**: Stores durable run metadata (`runs` table) and granular step-by-step telemetry (`steps` table) managed via async SQLAlchemy 2.0 and Alembic.
- **Consequences**:
  - Sub-millisecond session state retrieval and automatic cleanup of abandoned sessions.
  - Full structured telemetry preserved for debugging, auditing, and analytics without polluting fast memory.

---

## ADR-004: Declarative Tool Registration with Pydantic Introspection

- **Date**: 2026-09-28
- **Status**: Accepted
- **Context**: Defining tools manually in JSON Schema is error-prone and causes drift between code and documentation.
- **Decision**: Provide a `@tool` decorator that inspects native Python type hints and docstrings to automatically generate standard JSON Schemas and dynamic Pydantic argument validation models.
- **Consequences**:
  - Tools are idiomatic, typed Python functions.
  - Argument validation is strictly enforced before tool execution.
  - Structured validation errors can be fed back to the LLM for self-correction without crashing the runtime.

---

## ADR-005: Two-Tier Test Strategy with Server-Side Atomic Rate Limiting

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: The suite originally exercised `PostgresMemoryStore`, `PostgresTraceSink` and `RedisMemoryStore` exclusively against in-memory SQLite and `fakeredis`. Both stand-ins behave differently from their targets in ways that matter: SQLite renders `uuid`/`json`/`timestamptz` as `CHAR`/`TEXT`, ignores foreign keys unless `PRAGMA foreign_keys=ON`, and `fakeredis` is a pure-Python reimplementation that never exercises the hiredis codec or real TTL expiry. CI declared PostgreSQL and Redis service containers that no test connected to. A concurrency test written against live Redis then showed the sliding-window rate limiter admitting 19 of 20 simultaneous requests against a limit of 5, because `ZCARD` was read and `ZADD` written in separate round trips with an `await` between them.
- **Decision**:
  - Keep the hermetic tier as the default (`pytest`, SQLite + `fakeredis` + `FakeLLMClient`) so the suite stays fast and dependency-free.
  - Add an opt-in `real_infra` tier (`tests/real/`, enabled by `AGENTKIT_REAL_INFRA=1`) that runs the same components against live PostgreSQL and Redis, driving Alembic through the real dialect and running the real FastAPI lifespan.
  - Enforce the sliding-window limit with a single Lua script so trimming, counting and insertion are one indivisible operation, with a non-atomic fallback only when a deployment forbids scripting.
  - Give fakeredis a Lua runtime (`lupa`) so the hermetic tier exercises the same code path as production.
  - Split CI into a hermetic matrix job and a dedicated live-infrastructure job so the service containers are actually exercised.
- **Consequences**:
  - Dialect-specific and concurrency behaviour is covered rather than assumed; a rate-limit bypass of this class cannot regress unnoticed.
  - `pytest` gains an explicit opt-in for external services, documented in the README and `.env.example`.
  - The live tier truncates the `runs`/`steps` tables and deletes only `session:*` / `ratelimit:*` keys, so it requires a dedicated test database.

---

## ADR-006: Gemini 2.x/3.x Thought Signature Preservation and Active Model Standard

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: Newer Gemini reasoning models (e.g. Gemini 2.x and 3.x) employ internal reasoning/thinking steps before invoking tools. The Google GenAI API emits an opaque `thought_signature` on candidate `Part` objects containing `function_call`. When returning subsequent conversation turns with `function_response` parts, the API strictly enforces that the prior assistant turn echoes back the exact `thought_signature`. Omitting this signature produces `400 INVALID_ARGUMENT — Function call is missing a thought_signature in functionCall parts`. Additionally, older model identifiers like `gemini-2.5-flash` returned `404 NOT_FOUND` for new users, and `Role.TOOL` messages in the Gemini adapter previously used `role="user"` instead of the official `role="tool"` and mapped tool calls by arbitrary UUID IDs rather than declared function names.
- **Decision**:
  - Update `ToolCall` neutral model to include an optional `thought_signature: bytes | None = None` field.
  - In `GeminiClient`, extract and preserve `thought_signature` from candidate parts and forward it in subsequent assistant tool calls using `types.Part(function_call=..., thought_signature=...)`.
  - In `GeminiClient`, resolve `Role.TOOL` message identifiers to declared function names via assistant history, and use `types.Content(role="tool", parts=[...])` adhering to official Google GenAI SDK standards.
  - Update `RedisMemoryStore` to serialize messages via `m.model_dump(mode="json")` so raw bytes fields are transparently base64-encoded and decoded across session persistence.
  - Standardize the active default Gemini model to `gemini-2.0-flash` across application settings, factory, documentation, `.env.example`, and `docker-compose.yml`.
- **Consequences**:
  - Full compatibility with active and future reasoning-capable Gemini models.
  - Clean multi-turn tool calling without 400 or 404 errors.
  - Zero disruption to OpenAI or FakeLLM adapters since `thought_signature` defaults to `None`.

---

## ADR-007: Idempotent Automated Demo Database Seeding on Compose Boot

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: SPEC Section 13 mandates that `docker compose up` must start a working system, run migrations, and seed the demo database (`products` and `orders`). The previous migration service only ran `alembic upgrade head`, requiring manual or separate execution of demo seeds, while `scripts/seed_demo_db.sql` lacked conflict clauses and would crash with primary key collisions if re-executed on subsequent compose restarts.
- **Decision**:
  - Add `ON CONFLICT (id) DO NOTHING;` to `INSERT INTO products` and `INSERT INTO orders` in `scripts/seed_demo_db.sql` for strict ANSI-compliant idempotency.
  - Implement a dedicated `scripts/seed.py` executable that reads `scripts/seed_demo_db.sql` and applies it asynchronously using the configured `DATABASE_URL`.
  - Update `Dockerfile` to copy `scripts/` into the production runtime container.
  - Update `docker-compose.yml` migrations command to execute `alembic upgrade head && python scripts/seed.py` prior to the `api` service starting.
  - Delegate `examples/demo.py` seeding to `scripts.seed.seed_database` to eliminate code duplication.
- **Consequences**:
  - `docker compose up` boots directly into a fully seeded, turnkey environment ready for analytical queries via `sql_readonly`.
  - Database seeding is safely re-entrant across container restarts without data duplication or integrity errors.


---

## ADR-008: Fail-Closed Configuration and Dependency Resolution

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: A whole-project audit found several paths where a misconfiguration or a transient fault was silently converted into a working-looking success. `Settings.API_KEYS` defaulted to `ak_test_key_12345`, a key published in the repository, so any deployment without a `.env` came up openly accessible. `get_llm_client` caught every exception from the provider factory and returned `FakeLLMClient`, so a missing `GEMINI_API_KEY` produced HTTP 200 with scripted text instead of a 503. `get_agent` memoized the first assembled `Agent` on `app.state`, so after the first request every later request reused that request's LLM client, registry, memory store and trace sink, ignoring any override made afterwards. `RetryingLLMClient` existed with tests but was referenced nowhere in the request path, leaving production with no retry on transient provider failures. `TieredMemoryStore` forwarded only the run methods, so duck-typed `get_run_trace` detection in the trace endpoint fell through to a trace sink that may hold no rows.
- **Decision**:
  - Make `API_KEYS` required with no default; `Settings` validation rejects an empty or whitespace-only value.
  - Remove the `FakeLLMClient` fallback from the request path. Provider construction failure raises `ServiceUnavailableError` (503). `LLM_PROVIDER=fake` remains an explicit opt-in and short-circuits before the factory.
  - Wire `RetryingLLMClient` into `get_llm_client`, configurable through `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_DELAY_S` and `LLM_RETRY_MAX_DELAY_S`. An explicit `app.state.llm_client` override still wins and is not wrapped, keeping tests deterministic.
  - Build the `Agent` per request from resolved dependencies instead of caching it. An explicit `app.state.agent_override` is still honoured.
  - Forward `get_run_trace` and `list_runs` through `TieredMemoryStore` to the underlying run store, returning empty lists when the run store does not support them.
- **Consequences**:
  - A misconfigured deployment fails loudly at startup or on the first request instead of serving fake or unauthenticated traffic.
  - No request is served by a dependency graph assembled for an earlier request.
  - Transient provider failures (429/5xx/network) are retried with exponential backoff and jitter; authentication and other 4xx failures are not.
  - The test suite seeds a fixed `API_KEYS` before importing `agentkit.api.main`, whose module-level `create_app()` would otherwise fail validation.

---

## ADR-009: Post-Connect SSRF Verification and Bounded Resource Use in Tools

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: `http_fetch` resolved the hostname, checked the resolved addresses, then let `httpx` resolve the hostname again when connecting. That time-of-check/time-of-use gap lets a short-TTL DNS answer hand back a public address during validation and a loopback or `169.254.169.254` address during connection. DNS resolution was synchronous `socket.getaddrinfo` called directly from async code, stalling the event loop, and the full response body was buffered before being truncated to `max_chars`, so a large payload could exhaust memory. Separately, `sql_readonly` applied `fetchmany(row_limit)` client-side only, which still makes the database plan and execute the full scan, and its keyword scan rejected any query containing a write keyword even inside a string literal, while its bare `#` check rejected the legitimate PostgreSQL JSON operators `#>` and `#-`.
- **Decision**:
  - Resolve DNS via `asyncio.to_thread` and reuse the resolved addresses for both validation and the connection decision.
  - After connecting, read `server_addr` from the response's network stream and reject the request if the peer is not among the validated addresses.
  - Stream the body with an explicit byte budget instead of buffering then slicing.
  - Apply the row limit in SQL by wrapping an unbounded statement, leaving a caller-supplied top-level `LIMIT` untouched, and push `statement_timeout` to PostgreSQL where the dialect supports it.
  - Mask string literals and quoted identifiers before the keyword scan, and treat `#` as a comment marker only when it does not begin a JSON path operator.
- **Consequences**:
  - DNS rebinding to a private or metadata address is refused after the connection is established, not merely before it.
  - No blocking DNS on the event loop, and response memory is bounded regardless of `max_chars`.
  - Legitimate read-only queries that merely mention a write keyword as data, or use JSON path operators, are accepted; write statements remain blocked.

---

## ADR-010: Atomic Fallbacks and Domain-Error Boundaries

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: Two fallbacks reintroduced the exact races their primary paths had been written to eliminate, and several error boundaries leaked raw SDK exceptions. The rate limiter's non-scripting fallback trimmed and counted in one pipeline then inserted in a second, so concurrent requests between the two observed the same pre-insert cardinality and were admitted together. In both LLM adapters the `try` block wrapped only the SDK call, so response parsing escaped as raw provider exceptions: reading `raw_response.text` raises on a blocked prompt, an empty `choices`/`candidates` list raised `IndexError`, and malformed tool-call JSON was rewritten to `{"raw": ...}`, which later failed argument validation with a misleading "missing required field" message. `RetryingLLMClient.__getattr__` recursed infinitely when `_client` was absent, and `/health` echoed `str(exc)` from SQLAlchemy and Redis on an unauthenticated endpoint.
- **Decision**:
  - Implement the non-scripting rate limit fallback with `WATCH`/`MULTI`/`EXEC` optimistic locking, denying the request if contention never settles within the retry budget.
  - Wrap response parsing in both adapters inside the error boundary, re-raising domain errors unchanged and mapping anything else to `LLMProviderError`. Report malformed tool-call JSON as such instead of substituting a placeholder object.
  - Use full UUIDs for generated `tool_call_id` values; a truncated 32-bit id risks collisions within a long session.
  - Guard `RetryingLLMClient.__getattr__` against re-entering itself when `_client` was never assigned.
  - Return fixed client-facing strings from `/health` and log the underlying driver error instead.
- **Consequences**:
  - The fallback enforces the documented limit under concurrency, erring toward denial where it cannot decide.
  - Every provider fault is matchable on `LLMProviderError`, so HTTP status mapping stays correct for malformed as well as failed responses.
  - The unauthenticated health endpoint no longer discloses internal hostnames, ports or database names.
