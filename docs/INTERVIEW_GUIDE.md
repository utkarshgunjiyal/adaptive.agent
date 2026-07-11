# Interview Guide

Preparation for discussing Runner.ai V2. Answers are honest and specific — no
inflated metrics. Pair with [ARCHITECTURE_WALKTHROUGH.md](./ARCHITECTURE_WALKTHROUGH.md).

## 30-second pitch

> Runner.ai is an autonomous planner–executor agent runtime built on a
> deterministic RAG platform. It routes each request with cheap deterministic
> rules before ever calling an LLM, plans over a unified capability registry
> (internal tools + MCP servers), executes tools as *evidence* rather than
> answers, evaluates and repairs its own output within bounds, and streams the
> final answer token-by-token. When it isn't sure, it checkpoints and asks a
> human, then resumes the same run. It's containerized, observable, and
> deploys to a single VM behind HTTPS.

## 2-minute explanation

Runner.ai started as V1.5: a deterministic RAG backend — upload PDFs, they're
chunked/embedded/indexed, and questions are answered from retrieved evidence.
V2 adds an **agent runtime** on top without touching V1.5. A request first hits a
**Behavior Gate** that decides deterministically whether it can be answered
directly or needs planning. Context is built under a token budget from
conversation, memory, and vector search. Capabilities — internal adapters and
optional MCP tools — live in **one unified registry**, so the planner sees a
single catalog and never knows a tool's origin. Tools execute through an
**Execution Bridge** that normalizes every result into evidence; the model then
synthesizes a grounded answer, streamed over SSE. An **evaluator** judges the
draft and can trigger bounded repair or, crucially, a **human-in-the-loop pause**
— the run checkpoints and returns a checkpoint id, and `/agent/resume` continues
the same run once the human answers. The whole thing is production-hardened:
correlation ids, injectable metrics with a cardinality guard, per-route rate
limiting, disconnect-safe streaming, health/readiness, and a single-VM Caddy+HTTPS
deployment with scripts and smoke tests.

## 5-minute architecture explanation

Follow the 14 stages in ARCHITECTURE_WALKTHROUGH.md: edge/transport →
deterministic routing → context → capability retrieval → planning → execution →
answer+streaming → evaluation/repair → runtime outcomes/HITL → checkpoint/resume
→ SSE disconnect safety → MCP lifecycle → frontend state machine → observability.
Emphasize three seams: (1) **deterministic-before-LLM** routing, (2) the
**planner/executor split** with tools-as-evidence, (3) **checkpoint/resume** as
the backbone of HITL. Note the invariant that shaped everything: the agent layer
is config-free at import and depends on V1.5 one-way, so it's fully unit-testable
without infrastructure.

## Architecture Q&A

**What problem does it solve?** Turning documents + a conversation into grounded,
actionable answers with an agent that can *use tools and pause for humans*, not
just chat.

**Why not just a chatbot?** A chatbot maps prompt→text. Runner.ai routes, plans,
executes tools, grounds answers in retrieved evidence, self-evaluates, and can
suspend/resume with human approval — with explicit, inspectable state at each step.

**Why deterministic routing before LLM routing?** Most requests don't need a
planner. Deterministic rules are free, instant, testable, and bound cost/latency;
the LLM is reserved for where it adds value. It also makes behavior reproducible.

**Why a planner/executor split?** Separating *what to do* from *doing it* makes
runs bounded, inspectable, and debuggable, and lets execution be uniform across
tool sources. Free-form tool-calling is more flexible but far less controllable.

**Why a unified capability registry?** So internal tools, MCP tools, and future
sources are one platform. The planner/retriever see a single catalog; origin is a
composition detail. Adding MCP is composition, not a runtime change.

**Why MCP?** To extend capabilities from external, trusted servers via an open
protocol without embedding a vendor SDK in the runtime. MCP is an *adapter
boundary*: discovered tools become capabilities; execution normalizes to the same
`AdapterResult`.

**Why checkpoint/resume?** An autonomous agent must be able to stop and ask a
human (clarification/approval) and later continue the *same* run faithfully.
Checkpoints make that durable across restarts (Mongo backend).

**Why SSE instead of WebSockets?** Streaming is one-directional
(server→client tokens/events); SSE is simpler, works over plain HTTP/proxies,
auto-reconnects, and needs no bidirectional channel. Resume is a separate POST.
Trade-off: SSE can't stream client→server, which we don't need.

**Why are tool outputs not final answers?** Tools produce *evidence*. The model
synthesizes a grounded, cited answer from that evidence, so the response is
coherent and attributable rather than a raw dump.

**How does HITL work?** A repair decision (`ask_user_for_clarification` /
`human_review`) maps to a waiting outcome; the run checkpoints and returns a
`checkpoint_id`. The UI shows the matching panel; `/agent/resume` folds the
resolution into a fresh prompt and continues the same run.

**What happens when a tool fails?** The Execution Bridge returns a failed
`AdapterResult` (safe error code); the evaluator can retry/rerun (bounded),
return partial-with-warning, or defer. No crash, no leaked internals.

**What happens when the provider fails?** Provider failures are classified into a
safe taxonomy (stage/code/retryable/clarification) and surfaced as `failed` or
`waiting_for_user` — never a stack trace to the client (Phase 37).

**How do you avoid infinite reflection/repair loops?** `max_repair_rounds` bounds
local regeneration; non-local actions are recorded and deferred, not looped. Every
run provably terminates in one terminal outcome.

## Production Q&A

**How do you scale it?** Externalize state (managed MongoDB/Redis/Qdrant/MinIO),
run multiple stateless backend replicas behind the proxy, and use the **Redis**
rate-limiter backend so limits are shared. The single-VM compose is the demo
topology; the seams (config-driven stores, distributed limiter) are already there.

**What state is persistent?** MongoDB (checkpoints + app data), Qdrant (vectors),
MinIO (documents). **Ephemeral:** Redis (job queue/cache), Caddy certs
(re-issued), in-memory metrics.

**What breaks when Redis fails?** Ingest job queueing and the distributed rate
limiter degrade (the limiter fails *open*); agent runs and resume still work
(checkpoints are in Mongo). Readiness reports Redis down.

**What breaks when Qdrant fails?** Document retrieval loses vector evidence;
answers fall back to conversation/memory context. Readiness reports it down.

**How is idempotency handled?** Resume is guarded against double-application at
the durable (Mongo) store; a re-resumed/cancelled checkpoint returns `409`, and
the UI clears it. Runs carry a stable `run_id`.

**How is observability implemented?** Structured JSON logs with correlation ids;
an injectable `MetricsSink` (NoOp default, in-memory, or isolated Prometheus)
recording HTTP + runtime counters; `/metrics` served only when enabled and never
public.

**How are high-cardinality metrics avoided?** A label guard drops sensitive/
high-cardinality keys (user_id, run_id, prompt, …) and caps series per metric, so
metrics can't explode or leak identifiers.

**How is rate limiting distributed?** In-memory sliding window per process for
dev; a **Redis** fixed-window limiter for multi-replica production, keyed per
route + principal, returning `429` + `Retry-After`.

**How are secrets handled?** From environment/`.env` (git-ignored, never baked
into images); MinIO/LLM creds rotated for prod; MCP headers/env/working-dir never
enter a ToolSpec, event, metric label, or log. Env validation rejects placeholders.

**How do you prevent data leakage?** Prompts/payloads aren't logged
(`LOG_SENSITIVE=false`); health/errors never leak stack traces or connection
strings; the UI renders only curated safe fields; `/metrics` is internal.

**How do you cancel disconnected streams?** The SSE layer watches client
disconnect and cancels the background run task; no orphaned work, no terminal
event after disconnect.

**How do you deploy and roll back?** `scripts/deploy.sh` (validate→build→up→
health), `scripts/update.sh` (ff + redeploy), `scripts/rollback.sh` (previous
commit, data preserved). Data via `backup.sh`/`restore.sh`.

## Trade-offs Q&A

**What would you redesign with more time?** Real authentication (the current stub
is dev-only); externalized state for true horizontal scale; a richer embedding
reranker; and end-to-end tests that run the full Docker stack in CI.

**Why not Kafka?** No high-throughput event backbone is needed; Redis handles the
ingest queue. Kafka would add heavy ops for no current benefit.

**Why not Kubernetes?** A single VM meets the demo/interview goal with far less
complexity. The compose seams map cleanly to K8s later if scale demands it.

**Why not LangGraph for everything?** The runtime is deliberately explicit and
config-free so it's fully testable and inspectable. A framework would obscure the
state machine and add a dependency; the planner/executor/checkpoint seams are the
valuable part, and they're hand-built and small.

**Why not precompute every summary?** Cost and staleness. Summaries/retrieval are
computed under a budget per request; precomputing everything wastes work and goes
stale as documents change.

**Why not store everything in one database?** Each store fits its job: Mongo
(documents/records/checkpoints), Qdrant (vectors), MinIO (blobs), Redis (queue).
One database would be a poor fit for at least one workload.

**Why not expose every internal event to the UI?** Safety and clarity: internals
(RunContext, FinalPrompt, plan details) can contain sensitive or confusing data.
The UI shows a curated, safe subset by design.

**What is the current biggest limitation?** Authentication — the shipped
`get_current_user` is a development stub. The startup guard prevents it from
silently running in production, but real multi-user auth must be wired before
public exposure.

## Deep technical discussion outline

- The config-free-at-import invariant and how it enables infra-less unit tests.
- Runtime outcome derivation (`derive_runtime_outcome`) and the repair→outcome map.
- Checkpoint/resume faithfulness: same `run_id`, resolution folded into a fresh
  `FinalPrompt`, durable-store conflict handling (`409`).
- SSE seam: `stream_sink` in the orchestrator, live queue drain, disconnect cancel.
- Capability platform: sources → unified registry → by-kind execution bridge.
- MCP transport behind a Protocol; composition owns connection lifecycle.
- Metrics label guard and cardinality cap; why rate limiting is middleware, not a
  route dependency (config-free import boundary).

## Strongest engineering decisions

1. Deterministic routing before any LLM call — cheap, testable, bounded.
2. Planner/executor split with **tools-as-evidence** — controllable and grounded.
3. Checkpoint/resume as first-class — real HITL, durable across restarts.
4. One-way, config-free agent layer — the entire runtime is unit-testable without
   infrastructure or credentials (700+ tests, no Docker required).
5. Additive hardening (metrics/rate-limit/demo) default-off — dev/tests stay
   byte-identical while production gets real controls.

## Honest limitations

- Development auth stub (guarded, but not real auth).
- Single-VM topology (SPOF) until state is externalized.
- Deterministic providers for offline/demo produce stub answers, not real ones.
- No full-stack E2E in CI (compose validate + image build + unit/int tests only).
- Dev-only frontend advisories (vite/vitest) accepted; absent from the prod build.

## Future work

Real auth (OIDC/session) wired to the existing `get_current_user` seam;
externalized managed datastores + multiple backend replicas; embedding-based
capability rerank; TTL/cleanup for checkpoints; optional OpenTelemetry traces;
full-stack smoke test as a CI job.

## Likely interviewer follow-ups

- "Show me where a request becomes a plan." → `PlannerRuntime` + planner provider.
- "Prove the demo pause is real." → `DemoEvaluator` → repair→`waiting_for_approval`
  → checkpoint → `/agent/resume` (same `run_id`); tests in `test_demo_evaluator.py`.
- "What stops metrics from leaking user ids?" → `sanitize_labels` + forbidden set.
- "How is the agent testable without a DB?" → config-free imports; fakes; `asyncio.run`.
- "What happens on client disconnect mid-stream?" → task cancellation in the SSE layer.
