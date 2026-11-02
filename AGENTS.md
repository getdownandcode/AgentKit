# AgentKit Development Guidelines (AGENTS.md)

This file contains the mandatory development and operational rules for AgentKit. Re-read this file at the start of **EVERY** task.

---

## 1. Stack and Folder Structure

### Tech Stack
- **Language**: Python 3.11+ (Runtime target: Python 3.12+)
- **API**: FastAPI (async), Pydantic v2
- **Database & Migrations**: PostgreSQL via async SQLAlchemy 2.x, Alembic
- **Cache & Sessions**: Redis (async via `redis.asyncio`)
- **LLM Integrations**: Official SDKs for Google Gemini (`google-genai`) and OpenAI (`openai`)
- **Testing**: `pytest`, `pytest-asyncio`, `httpx` (LLMs mocked by default)
- **Tooling & Quality**: `ruff` (linter & formatter), `mypy` (strict type-checking)
- **Containerization & CI**: Docker, Docker Compose, GitHub Actions

### Folder Structure
```
agentkit/
├── agentkit/
│   ├── core/            # agent.py, loop logic, trace.py, types.py, errors.py
│   ├── llm/             # base.py, gemini.py, openai.py, factory.py, fake.py
│   ├── tools/
│   │   ├── registry.py
│   │   └── builtin/     # calculator.py, sql_readonly.py, read_file.py, http_fetch.py, web_search.py
│   ├── memory/          # base.py, redis_store.py, pg_store.py
│   ├── api/             # main.py, routes.py, schemas.py, deps.py, auth.py, ratelimit.py
│   ├── db/              # models.py, session.py
│   └── config.py        # Pydantic BaseSettings
├── migrations/          # Alembic migrations
├── scripts/             # seed_demo_db.sql and utilities
├── tests/
│   ├── unit/            # Unit tests for core, tools, llm, memory
│   └── integration/     # End-to-end API and workflow tests
├── examples/            # Demo agents and sample requests
├── docs/                # Architecture, Plan, Decisions, Progress, Specs
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── .github/workflows/ci.yml
└── README.md
```

---

## 2. Coding Standards

1. **Type Annotations**:
   - Complete type hints on every function argument and return value.
   - Code must pass `mypy --strict` or strict project settings.
2. **Pydantic Models at Boundaries**:
   - Every external boundary (API requests/responses, tool parameters/results, LLM interchange formats, DB DTOs) must use Pydantic v2 models or immutable dataclasses.
3. **Architecture & Composition**:
   - Small, pure functions with single responsibilities.
   - No god-classes: split reasoning loop, execution, tracing, and persistence into discrete decoupled components.
   - `core` depends only on abstract interfaces (`LLMClient`, `MemoryStore`, `TraceSink`), never concrete implementations.
   - Layering rule: `api` -> `core` -> `(llm, tools, memory, db)`. Never import `api` into lower layers.
   - No global state or singletons. Dependency injection via constructor arguments and FastAPI dependencies.
4. **Async & Concurrency**:
   - Async across the API and core runtime layer.
   - No blocking I/O calls directly in the event loop. Run CPU-bound or synchronous tools in threadpool executors (`asyncio.to_thread`).
5. **Error Handling**:
   - Never use bare `except:` or catch unhandled `Exception` silently.
   - Explicit domain exceptions defined in `agentkit/core/errors.py`.
   - Tools must catch their own operational errors and return structured `ToolResult(ok=False, error=...)` instead of raising into the ReAct loop.
6. **Logging & Security**:
   - Standard logging via `logging.getLogger(__name__)`. **Never use `print()`**.
   - Include contextual identifiers (`run_id`, `step_no`) in log records.
   - Never log secrets, API keys, or raw confidential prompt data at `INFO` level.
   - Zero hardcoded secrets in code; all configuration sourced from environment variables via `agentkit/config.py`.
7. **Documentation**:
   - Clear docstrings on all public classes, functions, and modules.
   - Comments must explain *why*, not *what*.

---

## 3. Testing Rules

- **Framework**: `pytest` and `pytest-asyncio`.
- **Mocked LLM by Default**: All unit and integration tests must run without real external LLM API calls using `FakeLLMClient`.
- **Test-First for Core Logic**: Write tests alongside or prior to core loop, registry, and tool features.
- **Coverage Target**: Minimum **85%** code coverage across `agentkit/core`, `agentkit/tools`, and `agentkit/llm`.
- **Security & Safety Tests**:
  - Calculator: AST validation (prohibit `eval`, `exec`, arbitrary attributes/imports).
  - SQL: Block non-`SELECT`, multiple statements, commented payloads, write keywords.
  - File Reader: Base directory allowlist enforcement, path traversal (`../`) and symlink escapes blocked.
  - HTTP Fetch: SSRF protection against loopback, private RFC1918 IPs, and AWS metadata endpoints (`169.254.169.254`).
  - Agent Loop: Max-step termination, timeout handling, repeated tool-call loop prevention.

---

## 4. Git Rules

- **Conventional Commits**: Format commit messages as:
  - `feat(<scope>): <description>`
  - `fix(<scope>): <description>`
  - `test(<scope>): <description>`
  - `docs(<scope>): <description>`
  - `chore(<scope>): <description>`
  - `refactor(<scope>): <description>`
- **Atomic Commits**: One logical change per commit.
- **Branching Strategy**:
  - Feature/task branches only: `feat/<slug>`, `fix/<slug>`, `test/<slug>`, `docs/<slug>`, `chore/<slug>`.
  - Branch lanes: `develop` is the integration lane, `fix/work-queue` is the starting lane for maintenance fixes, and `feat/roadmap` is the starting lane for feature work. Create a short-lived task branch from the appropriate lane and open its PR against `main`.
  - **Never commit directly to `main`** (except the initial warm-up scaffolding push).
  - **Never force-push `main`**.
  - All task changes must be opened as pull requests and merged via `gh pr merge`.

### Release and tag workflow
- Use Semantic Versioning. Keep `[project].version` in `pyproject.toml` aligned with the latest published release until a release PR proposes the next version.
- Before choosing a version, fetch tags and inspect the current release: `git fetch --tags`, `git tag --sort=-version:refname`, and `gh release view`.
- Bump the version and update release notes in a task branch; run the full quality gates and open a PR. Do not create a release tag before that PR is merged to `main`.
- After merge, verify `main` is current and the version is unused, then create an annotated tag on the merge commit: `git tag -a vX.Y.Z -m "AgentKit vX.Y.Z"`. Push the tag with `git push origin vX.Y.Z` and create the GitHub release with `gh release create vX.Y.Z --verify-tag --generate-notes`.
- For package artifacts, build and inspect before publishing: `python -m pip install build twine`, `python -m build`, and `python -m twine check dist/*`. Confirm the package registry name belongs to this repository and that the publishing identity is configured before any upload; never publish credentials or artifacts from an unreviewed branch.
- Registry check on 2026-11-03 found that PyPI's `agentkit` project metadata identifies a different maintainer and project. Do not upload this repository under that name unless ownership is confirmed; resolve the distribution name and update `pyproject.toml`, install instructions, and the trusted publisher configuration first.
- Never move or reuse a published tag. If a tag already exists, select the next unused version and record the correction in the release PR.

---

## 5. Definition of Done (DoD)

A task is complete only when:
1. All relevant unit and integration tests pass cleanly (`pytest`).
2. Code style and typing pass with zero errors (`ruff check .`, `ruff format --check .`, `mypy agentkit`).
3. Relevant documentation (docstrings, README, or docs/) is updated.
4. `docs/PROGRESS.md` is updated with task status, PR link, commit count, and notes.
5. If architectural decisions were made, they are recorded in `docs/DECISIONS.md`.
6. Task branch is pushed, Pull Request created, reviewed, and merged to `main`.
