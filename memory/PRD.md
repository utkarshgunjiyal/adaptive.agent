# Runner.ai — Product Requirements

## Original Problem Statement
Upgrade the existing Runner.ai repository into a production-grade Adaptive RAG
and tool-using agent. Preserve the existing FastAPI + React + MongoDB stack.
Every tool observation must return to the LLM as a ToolMessage so the LLM
chooses the next action — not a fixed plan generated up front.

## User Choices (2026-01)
- **LLM provider**: Emergent Universal LLM key today; provider abstraction
  supports `LLM_PROVIDER=emergent|anthropic|openrouter` via env, no code change.
- **Adaptive model**: Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`).
- **Checkpointer**: MongoDB via the official `langgraph-checkpoint-mongodb`
  (`AsyncMongoDBSaver`) on the existing cluster, isolated DB
  `runner_ai_langgraph`.
- **Rollback**: legacy `/api/agent/run/stream` stays reachable; adaptive is
  gated by the `ADAPTIVE_DEFAULT` env flag + backend feature-flag endpoint.
- **Testing**: legacy pytest suite must remain green throughout.

## Phase 1 — COMPLETE (2026-07-14)

### Restored bootability (isolated first change)
- `pydantic 2.9.2` was pinned but `pydantic-core 2.46.4` was installed →
  `ImportError: validate_core_schema`. Downgraded to `pydantic-core==2.23.4`
  (the exact pin `pydantic 2.9.2` declares). Backend now boots.
- Added missing runtime deps (`apscheduler`, `rank_bm25`, `reportlab`).

### Adaptive runtime (LangGraph)
- New package `app/adaptive/`:
  - `providers/base.py`, `providers/emergent.py`, `providers/__init__.py`
  - `tool_bindings.py` — Phase 1 binds `search_document_chunks`
  - `normalize.py` — `ToolObservation` w/ statuses
    `success|empty|failed|rejected|unavailable|uncertain`
  - `executor.py` — safe executor with timeout + secret redaction
  - `state.py`, `nodes.py`, `graph.py`, `config.py`
- New endpoint `POST /api/agent/run/adaptive/stream` (SSE)
- New endpoint `GET /api/agent/adaptive/config` (feature flag)

### Legacy preserved
- `services/agent.py`, `services/llm.py`, `services/thread_summary.py`,
  `services/hybrid_retrieval.py`, existing tool modules — unchanged.
- `routes/agent.py::_run_public` made lenient for adaptive rows.
- `models.ToolCallLog.status` extended (backward compatible).

### Frontend
- `api.js`: `streamAdaptiveRun` + `getAdaptiveConfig`.
- `ChatArea.js`: routes to adaptive when backend flag is on; runtime label
  displays `adaptive · claude-sonnet-4-5-20250929`.

### Verification (Phase 1)
- `pip check`: **No broken requirements found.**
- `import app.main`: **OK**.
- `GET /api/health` → `{"status":"ok"}`; `/api/ready` → mongodb True.
- Legacy pytest: **21/21 pass** (`tests/backend_test.py`).
- Adaptive pytest: **3/3 pass** (`tests/adaptive_phase1_test.py`).
- Bind-tools live spike PASS with Claude Sonnet 4.5.
- Frontend E2E: direct answer + document flow both green.
- MongoDB persistence: adaptive runs + LangGraph checkpoints verified.

## Architecture (current)

- **Backend** — FastAPI (`/app/backend`)
  - `app/routes/` — auth, threads, documents, agent (legacy),
    **adaptive_agent (new)**, tools, ops, digests, share
  - `app/services/` — thread_service, ingest, agent (legacy), hybrid_retrieval,
    llm, thread_summary, mcp, storage, embeddings, ocr, digest
  - `app/tools/` — registry, document_search, web_search, paper_search,
    user_preferences
  - `app/adaptive/` — providers, tool_bindings, normalize, executor, state,
    nodes, graph, config
- **Frontend** — React (CRA) + Tailwind + Sonner + lucide-react
- **DB** — MongoDB
  - `runner_ai.*` — application collections (unchanged)
  - `runner_ai_langgraph.checkpoints_aio / checkpoint_writes_aio` —
    durable LangGraph checkpointing

## Backlog

- **P0 Phase 2** — Bind arXiv + Tavily to the adaptive graph. Add capability
  re-selection when a tool returns `empty`/`failed`. Add bounded retries in
  the safe executor. Acceptance tests 3–5.
- **P0 Phase 3** — HITL. Wire `save_user_preference` + arXiv-paper import
  through `interrupt()` with the Mongo checkpointer.
- **P1 Phase 4** — Native token streaming, activity timeline UI, expanded
  observability, Anthropic + OpenRouter provider adapters.
- **P2** — Duplicate-tool-call detector, circuit breaker, prompt-injection
  test suite, run cancellation.

## Test credentials
See `/app/memory/test_credentials.md`.

## Next Actions
Phase 2 (adaptive multi-tool + capability reselection + failure recovery).
