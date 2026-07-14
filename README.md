# Runner.ai

A private AI research and knowledge platform. Sign in, upload PDFs, and chat with an autonomous agent that decides — under strict backend control — which tools to use: your private documents, the current web, or academic literature. Every answer is grounded in retrieved evidence with clickable citations.

> **Status:** V1.0 (Phases 1–4). Auth · PDF ingestion · document retrieval · web + arXiv search · streamed grounded chat · execution-details drawer. Phases 5–6 (structured planner refinements, MCP adapter, approvals UI, hybrid retrieval) are next.

---

## Stack

| Layer                 | Preview (this repo runs as-is) | Docker Compose "production"      |
| --------------------- | ------------------------------ | -------------------------------- |
| API                   | FastAPI + uvicorn              | FastAPI + uvicorn                |
| DB                    | **MongoDB (Motor)**            | **PostgreSQL** + Redis           |
| Vector store          | Mongo `chunks` collection + cosine | **Qdrant**                    |
| Object storage        | Local disk (`STORAGE_DIR`)     | **MinIO**                        |
| Background workers    | `asyncio.create_task`          | **Celery + Redis**               |
| LLM                   | User-owned key: OpenRouter (default) or Anthropic | same          |
| Frontend              | React (CRA) + Tailwind         | same, served by nginx            |

---

## Preview architecture

```
POST /api/agent/run/stream
    ├─ persist user message
    ├─ deterministic capability selection      (services/agent.select_tools)
    ├─ LLM planner → AgentPlan (JSON schema)   (services/agent.plan)
    ├─ policy validation                       (services/agent.validate_plan)
    ├─ executor · parallel + dependencies      (services/agent.execute_plan)
    │      ├─ search_document_chunks (Mongo cosine)
    │      ├─ get_document_summary
    │      ├─ list_user_documents
    │      ├─ web_search (Tavily)
    │      └─ paper_search (arXiv)
    ├─ evidence normalisation → EvidenceItem[]
    ├─ synthesizer prompt with [n]-citation rules
    └─ SSE stream · run_started · plan_ready · tool_call · answer_delta · run_completed
```

**Document ingestion** is asynchronous and runs off the request path:

```
POST /api/documents/upload
    → validate (magic bytes + size + type)
    → store on disk (per-user)
    → create Mongo document + job (status: queued)
    → asyncio.create_task(ingest_document)  # replaced by Celery in Docker mode
        → pypdf extract text page-by-page
        → chunk (1200 chars, 180 overlap)
        → embed (hashed n-gram) + upsert to Mongo `chunks`
        → gpt-5.2 summary
        → mark document ready
```

---

## Quick start (preview / this repo)

The preview container comes with all dependencies pre-installed. Backend and frontend are supervised.

```bash
sudo supervisorctl status
# backend    RUNNING
# frontend   RUNNING
# mongodb    RUNNING

# Open the public URL → /register → workspace.
```

### Environment (`/app/backend/.env`)

| Var                | Purpose                                       |
| ------------------ | --------------------------------------------- |
| `MONGO_URL`        | MongoDB connection string                     |
| `DB_NAME`          | Database name (`runner_ai`)                   |
| `JWT_SECRET`       | 64-char random hex                            |
| `LLM_PROVIDER`     | `openrouter` (default) \| `anthropic` \| `auto` \| `stub` |
| `LLM_MODEL`        | Provider-compatible model id (e.g. `anthropic/claude-3.5-sonnet`) |
| `OPENROUTER_API_KEY` | OpenRouter key (required when provider is `openrouter`) |
| `ANTHROPIC_API_KEY`  | Anthropic key (required when provider is `anthropic`)  |
| `TAVILY_API_KEY`   | Tavily web-search key (optional; tool marked unavailable if empty) |
| `MAX_UPLOAD_BYTES` | Ingest size cap (default 25 MB)               |
| `MAX_PAGES`        | Ingest page cap (default 200)                 |

### Environment (`/app/frontend/.env`)

| Var                    | Purpose                                          |
| ---------------------- | ------------------------------------------------ |
| `REACT_APP_BACKEND_URL`| Public backend URL (used by `axios` + SSE fetch) |

---

## Quick start (Docker Compose)

Runs the full spec: Postgres, Redis, Qdrant, MinIO, backend, Celery worker, and nginx-served frontend.

```bash
cp .env.example .env
# Set JWT_SECRET and your LLM credentials — OPENROUTER_API_KEY (default provider)
# or ANTHROPIC_API_KEY (and TAVILY_API_KEY if you want real web search).
docker compose up --build -d

# Health checks
curl http://localhost:8001/api/health
curl http://localhost:8001/api/ready
# UI
open http://localhost:3000
```

**NOTE:** the Docker stack image is a superset: the API image installs asyncpg/SQLAlchemy/celery/qdrant-client/minio in addition to the preview build's minimal deps. The preview and Docker builds share the same `server.py` entry point.

---

## Tool registry

| id                        | kind     | risk  | badge          | description                                     |
| ------------------------- | -------- | ----- | -------------- | ----------------------------------------------- |
| `search_document_chunks`  | internal | read  | private_doc    | **Hybrid** BM25 + dense + gpt-5.2 rerank search |
| `get_document_summary`    | internal | read  | private_doc    | Return the cached per-doc summary               |
| `list_user_documents`     | internal | read  | context        | List available documents + statuses             |
| `web_search`              | api      | read  | web_source     | Tavily web search (marked unavailable if no key)|
| `paper_search`            | api      | read  | research_paper | arXiv paper search                              |
| `get_user_preferences`    | internal | read  | context        | Read the user's saved preferences               |
| `save_user_preference`    | internal | **write** | context   | Persist a preference — **requires approval**    |
| `mcp_<label>_<name>`      | mcp      | read  | context        | Any tool auto-discovered from `MCP_SERVERS` env |

The registry lives in `backend/app/tools/registry.py`. Each tool exposes an executor callable and reports `available` based on config — a missing `TAVILY_API_KEY` makes `web_search` show as *unavailable* to the planner rather than fake success.

---

## API reference

| Method | Path                                | Purpose                                     |
| ------ | ----------------------------------- | ------------------------------------------- |
| POST   | `/api/auth/register`                | Create a new user                           |
| POST   | `/api/auth/login`                   | Sign in → JWT                               |
| POST   | `/api/auth/logout`                  | Stateless (client discards token)           |
| GET    | `/api/auth/me`                      | Current user profile                        |
| GET    | `/api/threads`                      | List user's threads                         |
| POST   | `/api/threads`                      | Create thread                               |
| GET    | `/api/threads/{id}`                 | Thread metadata                             |
| GET    | `/api/threads/{id}/messages`        | Message history                             |
| POST   | `/api/documents/upload`             | Upload PDF (multipart) → job                |
| POST   | `/api/documents/upload_bulk`        | Upload multiple PDFs at once                |
| GET    | `/api/documents`                    | List user's documents                       |
| GET    | `/api/documents/{id}`               | Document status + summary                   |
| POST   | `/api/documents/{id}/retry`         | Retry failed ingestion                      |
| GET    | `/api/jobs/{id}`                    | Ingestion job status                        |
| POST   | `/api/agent/run/stream`             | SSE agent run (planner + tools + synth)     |
| GET    | `/api/agent/threads/{id}/pending_approval` | Hydrate approval card after reload    |
| GET    | `/api/agent/runs/{id}`              | Full run trace (plan · tool calls · evidence) |
| POST   | `/api/agent/runs/{id}/approve`      | Approve a paused write/sensitive step       |
| POST   | `/api/agent/runs/{id}/reject`       | Reject a paused step                        |
| GET    | `/api/tools`                        | Public view of the tool registry            |
| GET    | `/api/digests/schedules`            | List user's digest schedules                |
| POST   | `/api/digests/schedules`            | Create schedule (topic + hourly/daily/weekly) |
| DELETE | `/api/digests/schedules/{id}`       | Delete a schedule                           |
| GET    | `/api/digests`                      | List past digest runs                       |
| POST   | `/api/threads/{id}/share`           | Enable public read-only sharing             |
| DELETE | `/api/threads/{id}/share`           | Revoke public sharing                       |
| GET    | `/api/share/{token}`                | Public shared thread (no auth, rate-limited)|
| GET    | `/api/health`                       | Liveness                                    |
| GET    | `/api/ready`                        | Deep readiness (Mongo ping)                 |

---

## Example prompts

```
"Summarize my uploaded architecture document."
"What does page 2 say about the executor?"
"Find recent arxiv papers about agentic RAG."
"Compare my uploaded RAG design with recent research."
"What is current news on MCP?"
```

---

## Safety / grounding contract

* Read-only tools run automatically. Write / sensitive tools (none shipped by default) require explicit user approval (`/agent/runs/{id}/approve`).
* The synthesizer prompt forbids answering from general knowledge when evidence is empty — it says so instead. This is enforced by policy in the system prompt; runs where every tool fails produce an honest "no evidence was retrieved" answer.
* All secrets are redacted from persisted logs and never returned to the frontend.
* Per-user rate limiting (auth + agent) is applied by an in-memory sliding-window limiter; swap in Redis for multi-worker.

---

## Frontend

The workspace is a three-column layout:

* **Left rail** — brand · New conversation · Threads / Documents tabs · user footer w/ logout
* **Middle** — sticky header (thread title + Execution toggle) · streamed chat · scoped-doc composer
* **Right drawer** — Execution details: capabilities selected · plan · tool calls · evidence · event stream · duration

Every interactive element has a `data-testid` (see `data-testid` audit in the codebase; used by the testing agent).

---

## What's next

Runner.ai V2 completes all P0/P1/P2 items surfaced in the V1 review — see the changelog in `/app/memory/PRD.md`. Remaining wishlist:

- Real embeddings provider (drop-in `services/embeddings.py`) when an OpenAI-compatible embeddings endpoint is wired to the configured LLM provider
- Redis-backed rate limiter + Celery worker deployment (already scaffolded in `docker-compose.yml`)
- Streaming `[n]` citation reconciliation at token-level
- OpenGraph metadata on shared-thread pages for LinkedIn / Twitter previews
