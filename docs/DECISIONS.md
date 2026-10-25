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

## ADR-008: Gemini Multi-Turn Function Response Role Specification and Model Standardization

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: The official Google Gemini GenAI SDK requires function execution responses in multi-turn chat history to be represented with `role="user"` wrapping `types.Part.from_function_response(...)`. Setting `role="tool"` triggers an upstream `400 INVALID_ARGUMENT` error because the Gemini API expects function outputs to be submitted as user turns containing function responses. Furthermore, Gemini API access tiers and quotas vary across model tiers; `gemini-3.1-flash-lite` provides ultra-low latency, robust tool calling, and high throughput without encountering regional or tier rate limitations.
- **Decision**:
  - Update `GeminiClient.chat()` to wrap function responses with `role="user"` using `types.Part.from_function_response(name=tool_name, response={"result": msg.content})`.
  - Standardize the default active Gemini model to `gemini-3.1-flash-lite` across `Settings`, `GeminiClient`, `create_llm_client`, `.env.example`, `docker-compose.yml`, and documentation.
  - Wire `RetryingLLMClient` with configurable `LLM_MAX_RETRIES` (default 3) and exponential backoff into `create_llm_client_from_settings()`.
- **Consequences**:
  - Eliminates 400 Invalid Argument errors on multi-turn ReAct tool execution with Gemini.
  - Consistent model behavior across local, containerized, and CI environments.
  - Automatic resilience against transient 429 quota exhaustion and 5xx network spikes.

---

## ADR-009: Fail-Closed Configuration and Dependency Resolution

- **Date**: 2026-10-01
- **Status**: Accepted
- **Context**: A whole-project audit found several paths where a misconfiguration or a transient fault was silently converted into a working-looking success. `Settings.API_KEYS` defaulted to `ak_test_key_12345`, a key published in the repository, so any deployment without a `.env` came up openly accessible. `get_llm_client` caught every exception from the provider factory and returned `FakeLLMClient`, so a missing `GEMINI_API_KEY` produced HTTP 200 with scripted text instead of a 503. The `app.state.agent` override was indistinguishable from the memoized first assembly that AUD-01 removed. `RetryingLLMClient` was wired by AUD-04 but with only a retry count: the backoff delays were fixed defaults rather than configurable settings. `TieredMemoryStore` forwarded only the run methods, so duck-typed `get_run_trace` detection in the trace endpoint fell through to a trace sink that may hold no rows.
- **Decision**:
  - Make `API_KEYS` required with no default; `Settings` validation rejects an empty or whitespace-only value.
  - Remove the `FakeLLMClient` fallback from the request path. Provider construction failure raises `ServiceUnavailableError` (503). `LLM_PROVIDER=fake` remains an explicit opt-in resolved by the factory.
  - Keep retry wrapping in `create_llm_client_from_settings`, now configurable through `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_DELAY_S` and `LLM_RETRY_MAX_DELAY_S`. An explicit `app.state.llm_client` override still wins and is not wrapped, keeping tests deterministic.
  - Assemble the `Agent` per request from resolved dependencies; an explicit `app.state.agent_override` remains the only override mechanism.
  - Forward `get_run_trace` and `list_runs` through `TieredMemoryStore` to the underlying run store, returning empty lists when the run store does not support them.
- **Consequences**:
  - A misconfigured deployment fails loudly at startup or on the first request instead of serving fake or unauthenticated traffic.
  - No request is served by a dependency graph assembled for an earlier request.
  - Transient provider failures (429/5xx/network) are retried with configurable exponential backoff and jitter; authentication and other 4xx failures are not.
  - The test suite seeds a fixed `API_KEYS` before importing `agentkit.api.main`, whose module-level `create_app()` would otherwise fail validation.

---

## ADR-010: Post-Connect SSRF Verification and Bounded Resource Use in Tools

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

## ADR-011: Atomic Fallbacks and Domain-Error Boundaries

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

---

## ADR-012: Dedicated Read-Only Role Is the Primary SQL Isolation Control

- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: `sql_readonly` connected with `Settings.DATABASE_URL`, the application's own superuser role, so the entire "read-only" guarantee rested on a keyword regex. That regex could be blinded by PostgreSQL quoting forms it did not understand: a dollar-quoted body (`$$'$$`) or an `E'\''` escape string hid quotes from the literal masker, and single-statement built-ins that read server files, execute dynamic SQL or mutate state (`pg_read_file`, `query_to_xml`, `setval`, `pg_terminate_backend`, `lo_import`, `dblink_exec`) never reached the keyword denylist at all. Separately, `LIMIT ALL` and any oversized `LIMIT` were treated as "already bounded" and passed through untouched, and the cap was only enforced by `fetchmany` on the client.
- **Decision**:
  - The tool connects with a dedicated, non-superuser role from `DATABASE_READONLY_URL`, provisioned by `scripts/init_readonly_role.py` with `CONNECT`, `USAGE` and `SELECT` only plus `default_transaction_read_only = on`. The application itself keeps using `DATABASE_URL`.
  - A missing read-only URL fails closed (`ToolSecurityError`) rather than falling back to the application role; each session additionally runs `SET TRANSACTION READ ONLY` and, for the configured engine, refuses a superuser connection before executing the statement.
  - Statement validation remains as defence in depth: the masker now understands dollar quotes and `E'...'` escapes, and a function-call denylist covers the side-effecting and file-reading built-ins.
  - A trailing `LIMIT`/`FETCH FIRST` bound is clamped to the configured maximum (`LIMIT ALL`, `LIMIT NULL` and oversized counts included), smaller caller bounds are preserved, and the statement is wrapped only when a row-locking clause forbids appending `LIMIT`.
- **Consequences**:
  - Writing through the tool requires two independent failures, one of them a database privilege error; a validation gap alone is no longer enough.
  - Deployments must provision the role (`python scripts/init_readonly_role.py`) or the SQL tool returns a structured failure. Compose does this in the migrations service.
  - An injected engine (demo, tests) is explicitly caller-owned and still gets the read-only transaction, but not the superuser check.

---

## ADR-013: Session History Is Durable, Bounded and Read Fail-Closed

- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Session persistence had three independent failure modes. `RedisMemoryStore.save_messages` serialized with `model_dump(mode="json")`, and Pydantic's default `ser_json_bytes="utf8"` raises `UnicodeDecodeError` on any non-UTF-8 Gemini `thought_signature` — so one signature aborted the write of the *entire* conversation. `get_messages` turned any deserialization error into `[]`, which made a corrupt history indistinguishable from a new conversation and let the next run overwrite it with the truncated one. And `truncate_messages` was never wired to a setting (histories grew without bound), while the naive tail cut it implemented could start the history on a `tool` message whose assistant `tool_calls` turn had been dropped, a shape providers reject.
- **Decision**:
  - Binary signatures are persisted with an explicit tagged base64 object (`{"base64": "..."}`), encoded in the store and decoded by a `ToolCall` field validator. Bare strings are still decoded as the legacy raw UTF-8 form so old histories remain readable.
  - A corrupt or unreadable history raises `SessionHistoryError` (503) instead of returning `[]`. `Agent.run` logs it with the session id, marks the run failed and never calls `save_messages`.
  - `SESSION_MAX_MESSAGES` (default 40) bounds the retained history and is wired through `get_memory_store`. Truncation advances the cut point past leading tool results, so the kept history is always provider-valid.
- **Consequences**:
  - A conversation can no longer be silently replaced by a context-free one after a decode error or a Redis blip; the caller gets a 503 it can retry.
  - Sessions are bounded in Redis, and the bound is enforced at the only writer.
  - A stored history written by an older version stays readable, at the cost of an explicit legacy branch in the validator.

---

## ADR-014: Gemini Adapter Preserves Provider Call Ids and Excludes Thought Parts

- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: The adapter emitted one `Content(role="user")` per tool result. Gemini validates that a turn answering a function-call turn carries the same number of `function_response` parts as the call turn had `function_call` parts, so every parallel tool call failed with `400 INVALID_ARGUMENT`. The provider's `FunctionCall.id` was discarded in favour of a fresh UUID and `FunctionResponse.id` was never set, so two parallel calls to the *same* tool could not be correlated at all. Separately, response parsing never inspected `Part.thought`, so internal reasoning text was concatenated into the answer, the persisted assistant turn and the API's `final_answer`; the `raw_response.text` fallback reintroduced it even after parts were filtered.
- **Decision**:
  - Consecutive `tool` messages are grouped into a single `Content(role="user")` with one `function_response` part per call, each carrying the id of the call it answers.
  - The provider's `FunctionCall.id` is preserved end to end (a fresh UUID remains the fallback when the provider omits it), and is forwarded on the outbound `FunctionCall`.
  - Parts with `thought=True` are excluded from the answer text but still yield their function call and thought signature. The `raw_response.text` fallback is used only when the response carried no inspectable parts.
- **Consequences**:
  - Parallel tool calls work against Gemini, and identical parallel calls can be matched to their results in call order.
  - Internal reasoning never reaches the client through `final_answer` or stored history.

---

## ADR-015: Provider Refusals Are a Distinct Terminal Status

- **Date**: 2026-10-25
- **Status**: Accepted
- **Context**: Neither adapter inspected the provider's own verdict. Gemini reports a blocked prompt through `prompt_feedback.block_reason` and a stopped generation through `candidate.finish_reason` (`SAFETY`, `RECITATION`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`); OpenAI reports `finish_reason == "content_filter"` or a populated `message.refusal`. All of these arrive as an ordinary 200 response with no content and no tool calls, and the agent only branched on "are there tool calls?", so each was returned as `SUCCEEDED` with an empty `final_answer`.
- **Decision**:
  - `LLMResponse` carries an optional `refusal_reason`, populated by each adapter from the provider's block/refusal signals.
  - `RunStatus` gains `refused`. The agent maps a refusal to that status with the provider's reason, and maps an empty completion (no text, no tool calls) to `failed` with an explicit "empty provider response" reason.
  - A `SUCCEEDED` run always has a non-empty `final_answer`; the assistant turn is only appended to history when there is text.
- **Consequences**:
  - Operator dashboards and clients can distinguish "the model declined" from "the system broke" and from a genuine empty answer.
  - The refusal reason is client-visible in `failure_reason`, which is safe: it is the provider's category, not prompt content.
