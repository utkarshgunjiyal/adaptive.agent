# Runner.ai — Product Requirements

## Original Problem Statement
Adaptive RAG + tool-using agent on top of the existing Runner.ai stack.
Every tool observation returns to the LLM as a ToolMessage.

## User Choices (2026-01)
- **LLM provider**: user-owned credentials only. Provider abstraction supports
  `LLM_PROVIDER=openrouter|anthropic` (plus `auto` / `stub`). OpenRouter is the
  default production provider because it allows model switching without code
  changes. No Emergent hosting / Universal Key / `emergentintegrations`.
- **Adaptive model**: read from `LLM_MODEL` / `LLM_MODEL_ADAPTIVE` — never
  hard-coded. For OpenRouter use e.g. `anthropic/claude-3.5-sonnet`.
- **Checkpointer**: MongoDB via official `langgraph-checkpoint-mongodb`
  (`AsyncMongoDBSaver`) on isolated DB `runner_ai_langgraph`.
- **Rollback**: legacy `/api/agent/run/stream` preserved.

## Status — Phases 1 + 2 + 3 COMPLETE (2026-07-14)

### Phase 1 (previous session)
Boot restore, provider abstraction, native `bind_tools`, direct answer,
one-tool document round, MongoDB checkpointer, feature flag, frontend.

### Phase 2 (this session)
- Bound arXiv, Tavily, list_user_documents, get_document_summary to the
  adaptive graph.
- Bounded capability reselection: after an empty/failed observation,
  add complementary source (Tavily when arXiv fails, arXiv when doc is
  empty, doc when web is empty). Capped at 2 reselections per run.
- Bounded read-only retries with exponential backoff (0.4s, 0.8s, 1.6s).
- Duplicate-call detection: sha256 fingerprint of (tool_id, canonical
  args). Duplicate invocations returned as `rejected` ToolMessage.
- arXiv rate-limit (HTTP 429) now returned as `unavailable` status.
- Overall run timeout enforced (90s default) with guarded final answer.
- Iteration/tool caps: 6 iterations, 8 total tool calls, 3 per tool.
- Legacy planner prompt fixed for arxiv query syntax.

### Phase 3 (this session)
- New tool `import_arxiv_paper` (approval-required). Wraps the existing
  `services.ingest.ingest_document` pipeline; idempotent per document_id.
- LangGraph `interrupt()` at policy_check node with proposals payload.
- Approval fingerprint binds a decision to exact (tool_id, canonical
  args). Modified args ⇒ new approval required.
- New endpoints:
  - `POST /api/agent/runs/{run_id}/adaptive/approve`
  - `POST /api/agent/runs/{run_id}/adaptive/reject`
  Both stream the resumed SSE.
- Backend enforces approval — the frontend cannot skip it.
- Rejected invocations return a `rejected` ToolMessage; the LLM writes
  an honest final answer explaining the rejection.
- Survives backend restart: durable Mongo checkpoint means any fresh
  saver instance resumes correctly.

### Phase 4 polish (this session)
- SSE vocabulary superset:
  `run_started, capabilities_selected, llm_thinking, tool_started,
   tool_completed, evidence_added, capability_reselected,
   waiting_approval, run_resumed, answer_delta, run_completed,
   run_failed`.
- Frontend activity timeline: `ExecutionDrawer` now renders adaptive
  events + a dedicated "Reselections" section with the reason and
  added tools.
- `ApprovalCard` reused for both legacy plan-step approval and
  adaptive proposals; runtime prop selects endpoint.

## Verification (this session)

- `pip check`: **No broken requirements found.**
- Legacy `tests/backend_test.py`: **20/21 pass**. The single flake
  (`test_paper_search_run`) requires arxiv to answer synchronously in
  25s; arxiv currently returns HTTP 429 (rate limit). The tool now
  reports `unavailable`, but the legacy test's `assert citations`
  cannot be satisfied without arxiv cooperating. External limitation,
  not a code regression.
- Adaptive Phase 1 tests: **3/3 pass**.
- Adaptive Phase 2 + 3 tests: **7/7 pass**
  (`tests/adaptive_phase23_test.py`):
  - `TestMultiSource::test_multi_source_comparison` — arxiv + doc citation
  - `TestFailureRecovery::test_arxiv_failure_triggers_reselection`
  - `TestEmptyResult::test_empty_result_returns_honest_answer`
  - `TestDuplicateDetection::test_duplicate_call_produces_final_answer`
  - `TestHITL::test_hitl_approve_flow` — proposal → approve → import ok
  - `TestHITL::test_hitl_reject_flow` — proposal → reject → no import
  - `TestHITL::test_hitl_resume_survives_saver_reload`
- Frontend production build: **success** (`yarn build` clean).
- Playwright smoke A: direct answer renders (759 chars), adaptive
  endpoint hit, runtime label `adaptive · claude-sonnet-4-5-20250929`.
- Playwright smoke B: document tool round renders grounded answer
  (689 chars) with `research_paper`/`your document` citation pill.
- Playwright smoke F: HITL import — approval card shown → approve →
  confirmation "Perfect! I've successfully imported the paper 'Attention
  Is All You Need' (arXiv:1706.03762)…", drawer shows
  `import_arxiv_paper OK 181ms · 1 evidence`.

## Architecture

- **Backend** — FastAPI (`/app/backend`)
  - `app/routes/adaptive_agent.py` — `/run/adaptive/stream`,
    `/runs/{id}/adaptive/{approve,reject}`, `/adaptive/config`
  - `app/adaptive/{providers,nodes,graph,state,executor,normalize,
    tool_bindings,capabilities,policy,config}.py`
  - `app/tools/paper_import.py` — HITL-gated adaptive tool
  - Legacy `app/routes/agent.py`, `app/services/agent.py`, etc. untouched
    except (a) planner prompt clarified, (b) `_run_public` tolerant of
    adaptive rows, (c) `ToolCallLog.status` extended.
- **Frontend** — React (CRA) + Tailwind
  - `api.js`: `streamAdaptiveRun`, `getAdaptiveConfig`,
    `streamAdaptiveApprove`, `streamAdaptiveReject`.
  - `ChatArea.js`: routes on feature flag; handles `waiting_approval`.
  - `ApprovalCard.js`: adaptive-aware.
  - `ExecutionDrawer.js`: adaptive events + reselections section.
- **DB** — MongoDB
  - `runner_ai.*` — application collections
  - `runner_ai_langgraph.checkpoints_aio / checkpoint_writes_aio`

## Deployment Readiness

- All services managed by supervisor: `frontend`, `backend`, `mongodb`,
  `redis` (unused by adaptive today), `code-server`, ingress.
- No mocks in code paths.
- Approval is enforced by backend policy, not frontend.
- Runtime limits enforceable via `ADAPTIVE_MAX_ITERATIONS`,
  `ADAPTIVE_MAX_TOOL_CALLS`, `ADAPTIVE_MAX_CALLS_PER_TOOL`,
  `ADAPTIVE_TOOL_TIMEOUT_S`, `ADAPTIVE_RUN_TIMEOUT_S`.
- Every SSE stream terminates: run_completed, run_failed, or (during
  approval) waiting_approval. Never leaves the frontend spinning.
- Legacy runtime available at `/api/agent/run/stream` for rollback.
- Frontend production build succeeds cleanly.

## Environment Variables

Backend `.env`:

```
MONGO_URL=mongodb://localhost:27017            # existing, protected
DB_NAME=runner_ai                              # existing, protected
JWT_SECRET=...                                 # existing
LLM_PROVIDER=openrouter|anthropic|auto|stub    # default: auto (prefers openrouter)
LLM_MODEL=anthropic/claude-3.5-sonnet          # provider-compatible model id
LLM_MODEL_ADAPTIVE=<optional adaptive-only model override>
OPENROUTER_API_KEY=<secret; required for openrouter>
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_HTTP_REFERER=<optional application URL>
OPENROUTER_APP_NAME=Runner.ai
ANTHROPIC_API_KEY=<secret; required for anthropic>
ADAPTIVE_DEFAULT=true
ADAPTIVE_MAX_ITERATIONS=6
ADAPTIVE_MAX_TOOL_CALLS=8
ADAPTIVE_MAX_CALLS_PER_TOOL=3
ADAPTIVE_TOOL_TIMEOUT_S=25
ADAPTIVE_RUN_TIMEOUT_S=90
TAVILY_API_KEY=<optional; adaptive/legacy web search disabled if absent>
```

Frontend `.env` (protected): `REACT_APP_BACKEND_URL`.

## Remaining Genuine Limitations

1. **Legacy `test_paper_search_run` flakes when arXiv rate-limits.** External
   dependency. The tool now degrades to `unavailable` cleanly; the test's
   `assert citations` requires arxiv to respond with data. 20/21 legacy
   tests stable.
2. **No token-level streaming** yet — `answer_delta` fires once at
   finalize. LiteLLM's native tool-calling doesn't cleanly interleave
   partial content with tool_calls in a single stream; deferred.
3. **Provider adapters** — OpenRouter (default) and the direct Anthropic API
   are both active, wired through LangChain `ChatOpenAI` / `ChatAnthropic`
   with native `bind_tools`. A `stub` provider runs with no network for
   local dev / CI. Emergent has been fully removed.
4. **Duplicate detection is exact-match** on canonical args. Slight
   variations (e.g. changed `top_k`) bypass it. Iteration caps prevent
   runaway loops.

## Test credentials
See `/app/memory/test_credentials.md`.
