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
