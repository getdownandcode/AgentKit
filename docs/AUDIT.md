# AgentKit Production Code & Infrastructure Audit Report

This audit documents every fake, stub, mock, placeholder, silent fallback, or unwired reliability mechanism identified across production code (`agentkit/`), examples (`examples/`), container definitions (`docker-compose.yml`, `Dockerfile`), and documentation (`README.md`).

Per the AgentKit specification and development rules:
> **Rule**: Mocks and fakes are permitted **ONLY** under `tests/`. Nothing under `agentkit/`, `examples/`, `docker-compose.yml`, or the `README.md` may depend on fake data, fake clients, or in-memory stand-ins, unless it is an explicit, documented option selected by configuration and never the default.

---

## 1. Audit Findings Table

| ID | Location | Identified Issue / Fake / Placeholder | Severity | Real Implementation Required | Status |
|:---|:---|:---|:---:|:---|:---:|
| **AUD-01** | `agentkit/api/deps.py:133-140` | `get_llm_client` catches all exceptions during LLM client creation and silently falls back to `FakeLLMClient()`. | **High** | Remove silent fallback to `FakeLLMClient` when configured with a live provider (`gemini`, `openai`). Raise the underlying error or `AuthenticationError` / `ConfigurationError`. Only return `FakeLLMClient` if `LLM_PROVIDER="fake"` is explicitly configured. | Resolved (PR #48) |
| **AUD-02** | `agentkit/api/deps.py:75-87` | `get_memory_store` silently falls back to `InMemoryMemoryStore()` when database session factory or Redis client is absent from `app.state`. | **High** | In production, require PostgreSQL and Redis. Raise `ServiceUnavailableError("database", ...)` when `session_factory` is missing, and `ServiceUnavailableError("redis", ...)` when `redis_client` is missing. Allow in-memory fallback only when explicitly injected via `app.state.memory_store` for tests. | Resolved (PR #48) |
| **AUD-03** | `agentkit/api/deps.py:95-99` | `get_trace_sink` silently falls back to `InMemoryTraceSink()` when database session factory is absent from `app.state`. | **High** | In production, require PostgreSQL persistence for traces. Raise `ServiceUnavailableError("database", ...)` when `session_factory` is missing. Allow in-memory fallback only when explicitly injected via `app.state.trace_sink` for tests. | Resolved (PR #48) |
| **AUD-04** | `agentkit/llm/factory.py:55-76` | `LLMRetryWrapper` (`RetryingLLMClient`) was built in `agentkit/llm/retry.py` but never wired into `create_llm_client_from_settings()`. Live LLM calls lack retries and backoff. | **Medium** | Wire `RetryingLLMClient` into `create_llm_client_from_settings()` for live providers (`gemini`, `openai`). Add `MAX_RETRIES` to `Settings` (default 3) and wrap client instances so transient 429/5xx errors automatically retry with exponential backoff and jitter. | Resolved (PR #49) |
| **AUD-05** | `examples/demo.py:151-158` | `build_llm()` silently falls back to `build_fake_llm()` when API key is missing, even when `--offline` or `--provider fake` is not specified. | **Medium** | Enforce explicit flags: raise `ValueError` requiring an API key unless `--offline` or `--provider fake` is explicitly specified by the operator. Pass configured API keys to `create_llm_client`. | Resolved (PR #50) |
| **AUD-06** | `README.md:103, 117` | Sample `.env` snippet contains unused configuration keys (`ENVIRONMENT=development`, `JWT_SECRET=...`). AgentKit uses `X-API-Key` headers rather than JWTs. | **Low** | Remove unused configuration lines from `README.md` to ensure documentation matches code reality. | Resolved (PR #50) |
| **AUD-07** | `agentkit/llm/gemini.py:105-118` | Upstream Gemini API requires function execution responses in multi-turn history to use `role="user"` wrapping `Part.from_function_response(...)`. Setting `role="tool"` triggers 400 Invalid Argument. | **High** | Update `GeminiClient.chat()` to wrap function responses with `role="user"` per Gemini API specification. Update unit tests. | Resolved (PR #51) |
| **AUD-08** | `docker-compose.yml:74` | `RUN_TIMEOUT_S` was hardcoded to 60s without environment exposure in docker compose, risking premature timeouts during complex multi-step live agent runs over high-latency networks. | **Medium** | Expose `RUN_TIMEOUT_S=${RUN_TIMEOUT_S:-60}` in `docker-compose.yml` to allow operator tuning. | Resolved (PR #52) |
| **AUD-09** | Repository-wide | Verification proof and model availability across live PostgreSQL 16, Redis 7, and Google Gemini API needed live validation with verbatim output logs. | **High** | Standardize active model to `gemini-3.1-flash-lite`, validate multi-step SQL analytical run, verify row persistence in PostgreSQL, test all 7 security guardrails live, and document verbatim results in `docs/E2E_REPORT.md`. | Resolved (PR #53) |

---

## 2. Component Health Verification Summary

| Component | Audit Check | Verdict | Notes |
|:---|:---|:---:|:---|
| **Gemini Adapter** (`agentkit/llm/gemini.py`) | Real SDK calls, token extraction, thought signature preservation | **PASS (after AUDIT-04/05/06)** | Uses official `google-genai` SDK `client.aio.models.generate_content`, extracts `usage_metadata` token counts, handles structured tool declarations. Earlier revisions of this table recorded an unqualified PASS while signatures could not be persisted, parallel function calls were rejected and thought parts leaked into the answer; those are fixed in the AUDIT remediation table below. |
| **OpenAI Adapter** (`agentkit/llm/openai.py`) | Real SDK calls, token extraction, tool calls | **PASS** | Uses official `openai` SDK `client.chat.completions.create`, extracts prompt/completion tokens, parses tool call JSON arguments. |
| **AST Calculator** (`agentkit/tools/builtin/calculator.py`) | No `eval`/`exec`, operator whitelist, DoS guards | **PASS** | Pure Python `ast` parser with strict node/exponent limits. |
| **Read-Only SQL Tool** (`agentkit/tools/builtin/sql_readonly.py`) | Single SELECT/WITH validation, DDL/DML rejection, real DB execution | **PASS (after AUDIT-01/02)** | No SQL parser is used: validation is a statement lexer (single `SELECT`/`WITH`, comment and separator scans on literal-masked text, function denylist for file/system built-ins). The primary control is the dedicated non-superuser role with `default_transaction_read_only` and `SELECT`-only grants; a superuser connection or missing `DATABASE_READONLY_URL` fails closed. The row cap is enforced in SQL, clamping `LIMIT ALL` and oversized limits. |
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

---

## 4. Consolidated AUDIT-01…18 Remediation (2026-10-25)

A second whole-codebase audit filed 18 standalone findings as GitHub issues (`AUDIT-01` …
`AUDIT-18`). They were fixed as a linear stack of nine branches; this table is the index, and
`docs/PROGRESS.md` records the per-layer detail.

| Finding | Severity | Issue | Branch | Resolution |
|:---|:---:|:---:|:---|:---|
| AUDIT-01 SQL tool runs as the app superuser | Critical | #58 | `fix/sql-readonly-isolation` | Dedicated non-superuser role, session read-only, superuser refused, quoting-mask and built-in denylist hardening |
| AUDIT-02 `LIMIT ALL` bypasses the row cap | Critical | #59 | `fix/sql-readonly-isolation` | Trailing bounds clamped in SQL (`ALL`/`NULL`/oversized), subquery bounds left alone |
| AUDIT-03 Trace sink fabricates a placeholder run | High | #60 | `fix/persistence-integrity` | Sink drops the step and logs; `create_run` is the only writer of run rows |
| AUDIT-04 `thought_signature` cannot be persisted | High | #61 | `fix/session-history` | Explicit tagged base64 codec plus `ToolCall` validator |
| AUDIT-05 Gemini parallel function calls broken | High | #62 | `fix/gemini-adapter` | One `Content` per call turn with one response part per call, provider ids preserved |
| AUDIT-06 Gemini thought parts leak into the answer | High/Medium | #63 | `fix/gemini-adapter` | `thought=True` parts excluded, guarded `raw_response.text` fallback |
| AUDIT-07 Refusals/blocks/empty responses succeed | Medium-High | #64 | `fix/provider-refusal-status` | `LLMResponse.refusal_reason`, `RunStatus.REFUSED`, explicit empty-response failure |
| AUDIT-08 Persistence errors swallowed | Medium-High | #65 | `fix/persistence-integrity` | `PersistenceError` (503), failed-state retry, no synthesised 200 |
| AUDIT-09 `session_id` unbounded | Medium-High | #66 | `fix/persistence-integrity` | `max_length=255` shared with the column via `SESSION_ID_MAX_LENGTH` |
| AUDIT-10 Session-history load failure is destructive | Medium-High | #67 | `fix/session-history` | `SessionHistoryError` (503), run marked failed, history never overwritten |
| AUDIT-11 Truncation unwired and orphans tool messages | Medium | #68 | `fix/session-history` | `SESSION_MAX_MESSAGES` wired, cut point advanced past leading tool results |
| AUDIT-12 `TOOL_OUTPUT_MAX_CHARS` ignored | Medium | #69 | `fix/tools-resources-config` | Wired into `AgentConfig`; compose forwards every documented setting |
| AUDIT-13 Tool schemas drop `$defs` | Medium | #70 | `fix/tools-resources-config` | `$defs` and related keys preserved, `$ref`s resolve |
| AUDIT-14 Provider clients per request, never closed | Medium | #71 | `fix/tools-resources-config` | One app-owned client, closed on shutdown |
| AUDIT-15 Duplicate detector only catches consecutive repeats | Medium | #72 | `fix/agent-loop-and-auth` | Sliding window of recent signatures |
| AUDIT-16 Documentation asserts guarantees the code lacks | Medium | #73 | `docs/correct-audit-claims` | README/ARCHITECTURE/AUDIT/ADR-006/PROGRESS/PLAN corrected, ADRs 012–017 added |
| AUDIT-17 API key comparison not constant-time | Low | #74 | `fix/agent-loop-and-auth` | `hmac.compare_digest` over normalised keys, no short-circuit |
| AUDIT-18 Compose publishes datastores with weak credentials | Low/Deployment | #75 | `chore/compose-hardening` | Loopback binds, required passwords, Redis auth, fail-closed credential guard |

Follow-up on the same stack: the hardened Redis required a password that nothing outside compose
consumed, so the live tier failed at fixture setup (`HELLO must be called with the client already
authenticated`, 18 errors). `REDIS_PASSWORD` now completes a credential-less `REDIS_URL` for the
application and the suite (`fix/real-infra-redis-auth`, ADR-018).
