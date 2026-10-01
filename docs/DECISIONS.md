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

