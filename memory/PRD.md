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
- Structured LLM planner via JSON schema response_format (stronger typing)
- Multi-turn thread summaries (incremental — currently use last 6 messages as context)
- Approvals UI (backend already checks; no write tools yet)
- MCP adapter (generic client for external MCP servers)
- Hybrid retrieval (BM25 + dense) + reranker
- Real embedding provider (e.g. text-embedding-3-small) when available in Emergent LLM key
- Test suite (pytest + Playwright)

## Test credentials
`/app/memory/test_credentials.md`

## Next Actions
See finish summary.
