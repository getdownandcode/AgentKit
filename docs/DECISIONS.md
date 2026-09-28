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
