# Runner.ai — Product Requirements

## Original Problem Statement
Build a production-quality full-stack autonomous AI research platform named **Runner.ai**. A user signs in, uploads private PDFs, and chats with an agent that decides (under strict backend control) which tools to use: private-document retrieval, web search, research-paper search, or a combination. Distinguish four source classes: **private document · research paper · web · conversation context**. Every answer must be grounded and cite the retrieved evidence.

## User Choices (2026-01)
- Runtime: **Preview build** (FastAPI + React + MongoDB + in-process background tasks + Mongo-backed vector store) **AND** a full `docker-compose.yml` for local Postgres/Redis/Celery/Qdrant/MinIO stack.
- LLM: **gpt-5.2** via **Emergent Universal LLM key**.
- Web search: **Tavily** (`TAVILY_API_KEY` provided); Papers: **arXiv** (no key).
- Auth: **JWT + bcrypt** (email/password).
- Delivery order: **Phases 1–4 first**, Phase 5–6 (planner details, MCP adapter, approvals UI) as follow-ups.

## Architecture (preview)
- **Backend** — FastAPI (`/app/backend`)
  - `app/routes/` — auth, threads, documents, agent (SSE), tools, ops
  - `app/services/` — thread_service, ingest, agent (planner/executor/synth), embeddings, vector_store, storage, llm
  - `app/tools/` — registry, document_search, web_search (Tavily), paper_search (arXiv)
  - `app/auth.py` — bcrypt + JWT + per-user in-memory rate limiter
  - `app/models.py` — Pydantic models incl. `AgentPlan`, `PlanStep`, `EvidenceItem`, `ToolCallLog`, `AgentRunPublic`
- **Frontend** — React (CRA) + Tailwind + Sonner + lucide-react
  - `src/pages/LandingPage.js`, `LoginPage.js`, `RegisterPage.js`, `WorkspacePage.js`
  - `src/components/ThreadSidebar.js`, `DocumentPanel.js`, `ChatArea.js`, `ExecutionDrawer.js`
  - `src/api.js` — axios + SSE-over-fetch client for `/agent/run/stream`
- **DB** — MongoDB (`runner_ai`). Collections: `users`, `threads`, `messages`, `documents`, `jobs`, `chunks`, `agent_runs`, `user_preferences`, `tool_calls`, `evidence_items`, `approval_requests`.
- **Docker Compose** — `/app/docker-compose.yml` ships Postgres, Redis, Qdrant, MinIO, Celery worker, and nginx-served frontend (superset image; not runnable in preview).

## Data isolation
Every collection is queried with `user_id` and thread ownership is checked before any read. Documents are stored on disk under `<STORAGE_DIR>/<user_id>/…` — no cross-user path.

## V2 (2026-01-13) — job-ready release

**Every P0/P1/P2 improvement from the V1 review has been implemented and tested (11/11 new tests, 21/21 V1 regression):**

- ✅ Structured JSON planner via schema-validated `complete_json` (fallback path kept)
- ✅ Hybrid retrieval — BM25 + hashed-dense + Reciprocal Rank Fusion + gpt-5.2 rerank
- ✅ Incremental thread summaries (`thread_summaries` collection · summarised in batches of 8)
- ✅ Approval workflow — `save_user_preference` write tool + amber ApprovalCard + resume-with-timeout
- ✅ Generic MCP HTTP adapter with boot-time discovery (`MCP_SERVERS` env)
- ✅ OCR fallback via pytesseract for scanned PDFs
- ✅ Proxy-aware rate limiter (`X-Forwarded-For` + `X-Real-IP`) + public share-endpoint limiter
- ✅ Multi-file drag-and-drop upload (`/api/documents/upload_bulk`)
- ✅ Share thread — public read-only `/share/:token` route + revoke + copy fallback for headless
- ✅ Cost/latency stats on ExecutionDrawer completion card (duration · tools · evidence · per-tool ms)
- ✅ Research digest — APScheduler in-process (rehydrates on startup) + `hourly | daily | weekly` cadences
- ✅ Approval card hydration on page reload (`GET /agent/threads/{id}/pending_approval`)
- ✅ Lenient plan-schema loader on resume (no 500s on schema drift)
- ✅ `asyncio.wait_for(90s)` timeout guard around approve-resume path

## What's Implemented (2026-01-13)
- ✅ JWT/bcrypt auth (`/api/auth/register|login|me|logout`) — rate limited per client
- ✅ Threads + messages (`/api/threads*`) with per-user ownership checks
- ✅ PDF upload → validate → disk store → Mongo `documents` + `jobs` → async ingest
- ✅ pypdf text extraction, chunking (1200/180), deterministic hashed embeddings, Mongo vector store with cosine search
- ✅ gpt-5.2 document summaries generated after ingestion
- ✅ Tool registry with 5 tools: `search_document_chunks`, `get_document_summary`, `list_user_documents`, `web_search` (Tavily), `paper_search` (arXiv)
- ✅ Deterministic capability selector + gpt-5.2 planner emitting JSON `AgentPlan` + policy validator
- ✅ Parallel executor with dependencies, per-tool timeouts, normalised `EvidenceItem`s
- ✅ Grounded synthesizer streaming `[n]` citations via SSE
- ✅ Full run inspection (`GET /api/agent/runs/{id}`) + approve/reject endpoints for future write tools
- ✅ Frontend: landing, login/register, workspace (3-column) with execution drawer
- ✅ Docker-compose stack (Postgres/Redis/Qdrant/MinIO/Celery/nginx) for local production deploy
- ✅ QA: 21/21 backend pytest + full E2E Playwright flow passing (`/app/test_reports/iteration_1.json`)

## Post-V1 fixes (2026-01-13)
- Documents fetched on workspace mount → composer "N DOC READY" counter accurate without opening Documents tab.

## Backlog (P1)
- Real embedding provider (e.g. text-embedding-3-small) when available in the Emergent LLM key — swap in `services/embeddings.py`
- Persistent Redis-backed rate limiter for multi-worker deployments
- OpenGraph meta tags for shared-thread previews
- Streaming `[n]` citations that reconcile mid-token (currently reconcile after `evidence_ready`)

## Test credentials
`/app/memory/test_credentials.md`

## Next Actions
See finish summary.
