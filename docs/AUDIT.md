# AgentKit Production Code & Infrastructure Audit Report

This audit documents every fake, stub, mock, placeholder, silent fallback, or unwired reliability mechanism identified across production code (`agentkit/`), examples (`examples/`), container definitions (`docker-compose.yml`, `Dockerfile`), and documentation (`README.md`).

Per the AgentKit specification and development rules:
> **Rule**: Mocks and fakes are permitted **ONLY** under `tests/`. Nothing under `agentkit/`, `examples/`, `docker-compose.yml`, or the `README.md` may depend on fake data, fake clients, or in-memory stand-ins, unless it is an explicit, documented option selected by configuration and never the default.

---

## 1. Audit Findings Table

| ID | Location | Identified Issue / Fake / Placeholder | Severity | Real Implementation Required | Status |
|:---|:---|:---|:---:|:---|:---:|
| **AUD-01** | `agentkit/api/deps.py:133-140` | `get_llm_client` catches all exceptions during LLM client creation and silently falls back to `FakeLLMClient()`. | **High** | Remove silent fallback to `FakeLLMClient` when configured with a live provider (`gemini`, `openai`). Raise the underlying error or `AuthenticationError` / `ConfigurationError`. Only return `FakeLLMClient` if `LLM_PROVIDER="fake"` is explicitly configured. | Pending |
| **AUD-02** | `agentkit/api/deps.py:75-87` | `get_memory_store` silently falls back to `InMemoryMemoryStore()` when database session factory or Redis client is absent from `app.state`. | **High** | In production, require PostgreSQL and Redis. Raise `ServiceUnavailableError("database", ...)` when `session_factory` is missing, and `ServiceUnavailableError("redis", ...)` when `redis_client` is missing. Allow in-memory fallback only when explicitly injected via `app.state.memory_store` for tests. | Pending |
| **AUD-03** | `agentkit/api/deps.py:95-99` | `get_trace_sink` silently falls back to `InMemoryTraceSink()` when database session factory is absent from `app.state`. | **High** | In production, require PostgreSQL persistence for traces. Raise `ServiceUnavailableError("database", ...)` when `session_factory` is missing. Allow in-memory fallback only when explicitly injected via `app.state.trace_sink` for tests. | Pending |
| **AUD-04** | `agentkit/llm/factory.py:55-76` | `LLMRetryWrapper` (`RetryingLLMClient`) was built in `agentkit/llm/retry.py` but never wired into `create_llm_client_from_settings()`. Live LLM calls lack retries and backoff. | **Medium** | Wire `RetryingLLMClient` into `create_llm_client_from_settings()` for live providers (`gemini`, `openai`). Add `MAX_RETRIES` to `Settings` (default 3) and wrap client instances so transient 429/5xx errors automatically retry with exponential backoff and jitter. | Pending |
| **AUD-05** | `examples/demo.py:151-158` | `build_llm()` silently falls back to `build_fake_llm()` when API key is missing, even when `--offline` or `--provider fake` is not specified. | **Medium** | Enforce explicit flags: raise `ValueError` requiring an API key unless `--offline` or `--provider fake` is explicitly specified by the operator. Pass configured API keys to `create_llm_client`. | Pending |
| **AUD-06** | `README.md:103, 117` | Sample `.env` snippet contains unused configuration keys (`ENVIRONMENT=development`, `JWT_SECRET=...`). AgentKit uses `X-API-Key` headers rather than JWTs. | **Low** | Remove unused configuration lines from `README.md` to ensure documentation matches code reality. | Pending |

---

## 2. Component Health Verification Summary

| Component | Audit Check | Verdict | Notes |
|:---|:---|:---:|:---|
| **Gemini Adapter** (`agentkit/llm/gemini.py`) | Real SDK calls, token extraction, thought signature preservation | **PASS** | Uses official `google-genai` SDK `client.aio.models.generate_content`, extracts `usage_metadata` token counts, handles structured tool declarations. |
| **OpenAI Adapter** (`agentkit/llm/openai.py`) | Real SDK calls, token extraction, tool calls | **PASS** | Uses official `openai` SDK `client.chat.completions.create`, extracts prompt/completion tokens, parses tool call JSON arguments. |
| **AST Calculator** (`agentkit/tools/builtin/calculator.py`) | No `eval`/`exec`, operator whitelist, DoS guards | **PASS** | Pure Python `ast` parser with strict node/exponent limits. |
| **Read-Only SQL Tool** (`agentkit/tools/builtin/sql_readonly.py`) | Single SELECT/WITH validation, DDL/DML rejection, real DB execution | **PASS** | AST/regex validation blocking DDL/DML, executes against SQLAlchemy engine, returns markdown. |
| **Sandboxed File Reader** (`agentkit/tools/builtin/read_file.py`) | Canonical path containment, symlink escape checks | **PASS** | Resolves real canonical paths, blocks traversal (`../`) and escaping symlinks. |
| **HTTP Fetch Tool** (`agentkit/tools/builtin/http_fetch.py`) | SSRF defense, RFC 1918 blocking, redirect safety | **PASS** | Validates DNS IPs on every hop, blocks loopback/private/metadata subnets, real `httpx` streaming. |
| **Web Search Tool** (`agentkit/tools/builtin/web_search.py`) | Real network calls to Tavily API | **PASS** | Makes real `httpx` POST calls, parses results or surfaces informative missing key errors. |
| **Execution Latency & Tracing** (`agentkit/tools/executor.py`, `agentkit/core/agent.py`) | Real measured latency | **PASS** | Measures execution duration using `time.perf_counter()`, records real `latency_ms` and tokens in `StepTrace`. |
| **Rate Limiter** (`agentkit/api/ratelimit.py`) | Atomic Redis sliding window | **PASS** | Runs atomic Lua script on Redis sorted sets, microsecond timestamps, returns 429 with `Retry-After`. |
| **Docker Compose & Migrations** (`docker-compose.yml`, `scripts/seed.py`) | PostgreSQL 16, Redis 7, Alembic migration + auto-seed on boot | **PASS** | Multi-stage container, healthcheck probes, migration and idempotent seed on startup. |

---

## 3. Remediation Roadmap

1. **Fix Batch 1 (AUD-01, AUD-02, AUD-03)**:
   - Branch: `fix/api-deps-strict-infrastructure`
   - Update `agentkit/api/deps.py` to eliminate silent fallbacks to `FakeLLMClient`, `InMemoryMemoryStore`, and `InMemoryTraceSink` when running without explicit test injection.
   - Tests: Integration test verifying 503 Service Unavailable when DB/Redis unconfigured, and error surfacing when live LLM provider fails.
2. **Fix Batch 2 (AUD-04)**:
   - Branch: `fix/wire-llm-retry-wrapper`
   - Add `MAX_RETRIES` to `agentkit/config.py`.
   - Wire `RetryingLLMClient` into `create_llm_client_from_settings` in `agentkit/llm/factory.py`.
   - Tests: Unit tests verifying wrapped client and retry behavior with backoff.
3. **Fix Batch 3 (AUD-05, AUD-06)**:
   - Branch: `fix/demo-strict-mode-and-docs`
   - Update `examples/demo.py` to enforce explicit `--offline` or `--provider fake` flags and raise `ValueError` on missing API keys.
   - Clean up `README.md` sample `.env`.
   - Tests: Integration tests verifying `demo.py` CLI and validation behavior.
4. **Phase 3 End-to-End Proof**:
   - Start live Docker Compose stack (`docker compose up -d`).
   - Run end-to-end demo and security/edge-case tests against live services.
   - Generate `docs/E2E_REPORT.md` with verbatim live output.
