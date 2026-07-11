# Project Positioning

Resume, LinkedIn, and repository copy for Runner.ai. Claims are capability-based,
not metric-based — no invented performance/impact numbers.

## One-line description

> Runner.ai — an autonomous planner–executor AI agent runtime with hybrid
> deterministic/LLM orchestration, human-in-the-loop checkpoint/resume, an MCP
> capability platform, token streaming, and production observability.

## GitHub repository description (≤ 350 chars)

> Autonomous AI agent runtime on a deterministic RAG platform: deterministic-
> before-LLM routing, planner/executor with tools-as-evidence, unified capability
> registry (internal + MCP), checkpoint/resume HITL, SSE token streaming, and
> production hardening (metrics, rate limiting, health, Caddy+HTTPS single-VM
> deploy). FastAPI · React/TS · Mongo · Redis · Qdrant · MinIO.

## Resume bullets (three variants)

**Variant A — systems/architecture focus**
- Designed and built an autonomous planner–executor agent runtime on a FastAPI/
  MongoDB/Redis/Qdrant/MinIO RAG platform, with deterministic routing before any
  LLM call, a planner/executor split, and tools consumed as evidence rather than
  answers.
- Implemented durable human-in-the-loop **checkpoint/resume** (Mongo-backed) so
  runs pause for clarification/approval and continue the same run faithfully.
- Shipped production hardening: correlation ids, injectable metrics with a
  cardinality/PII label guard, per-route rate limiting (Redis), disconnect-safe
  SSE streaming, health/readiness, and a single-VM Caddy+HTTPS deployment.

**Variant B — product/agent focus**
- Built an AI agent that plans, calls tools, grounds answers in retrieved
  evidence, streams tokens live over SSE, and asks a human when unsure — with an
  explicit, inspectable runtime state machine.
- Created a **unified capability registry** so internal tools and external **MCP**
  servers appear as one catalog to the planner; adding a capability source is
  composition, not a runtime change.
- Delivered a React/TypeScript UI that renders only safe runtime metadata and
  drives checkpoint-based resume — no business logic in the client.

**Variant C — engineering-rigor focus**
- Enforced a one-way, config-free agent layer that is fully unit-testable without
  any infrastructure or credentials (**700+ backend tests**, 30 frontend),
  keeping the existing V1.5 platform untouched.
- Bounded self-repair (evaluate → repair, capped rounds) so runs always terminate
  in one of a small set of typed outcomes — no unbounded reflection loops.
- Automated deployment with auditable scripts (validate-env, deploy, rollback,
  backup/restore, smoke tests) and CI (pytest, typecheck/lint/build, shellcheck,
  compose + reverse-proxy validation, image builds).

## LinkedIn launch post (concise)

> I built **Runner.ai** — an autonomous AI agent runtime, not another chatbot.
>
> It routes each request with deterministic rules *before* touching an LLM, plans
> over a unified catalog of internal + MCP tools, executes those tools as evidence
> (not answers), streams the final grounded answer token-by-token, and — when it
> isn't sure — checkpoints and asks a human, then resumes the same run.
>
> It's production-minded: correlation ids, metrics with a PII/cardinality guard,
> rate limiting, disconnect-safe streaming, health checks, and a single-VM
> HTTPS deployment with backup/restore and smoke tests.
>
> Stack: FastAPI · React/TypeScript · MongoDB · Redis · Qdrant · MinIO · Docker.
>
> Writeup + demo 👇

## LinkedIn technical post (deeper)

> Some engineering decisions I'm happy with in **Runner.ai**, my autonomous agent
> runtime:
>
> • **Deterministic before LLM.** A Behavior Gate decides DIRECT vs PLANNER with
>   cheap keyword rules first — most requests never need a planner. Free, instant,
>   testable, and it bounds cost.
>
> • **Planner/executor split, tools-as-evidence.** Separating *what to do* from
>   *doing it* makes runs bounded and inspectable. Tools return evidence; the model
>   synthesizes a grounded answer.
>
> • **Checkpoint/resume as a first-class citizen.** HITL isn't a hack — a repair
>   decision maps to a waiting outcome, the run persists a checkpoint (Mongo), and
>   resume continues the *same* run id.
>
> • **Config-free agent layer.** The whole runtime imports no settings and depends
>   on the platform one-way, so 700+ tests run with just pytest — no DB, no keys.
>
> • **Unified capability registry + MCP.** Internal tools and MCP servers are one
>   catalog; the planner never knows the origin. MCP is an adapter boundary, not a
>   second runtime.
>
> Plus the unglamorous-but-essential: injectable metrics with a cardinality guard,
> Redis rate limiting, SSE that cancels work on disconnect, and a Caddy+HTTPS
> single-VM deploy with scripts and smoke tests.

## Project demo description (for a portfolio card / repo top)

> A live agent that plans, uses tools, streams a grounded answer, and pauses for
> your approval before continuing — then resumes exactly where it left off.
> Deterministic demo mode runs fully offline (no API key) and shows real
> checkpoint/resume, not a scripted animation.

## Recruiter-friendly summary

> Runner.ai is a from-scratch AI agent platform showing senior-level backend and
> systems skills: an autonomous agent that plans and uses tools, a human-approval
> workflow, live streaming, and full production packaging (containers, HTTPS,
> monitoring, deployment automation). Built with FastAPI, React/TypeScript, and a
> real data stack (MongoDB, Redis, Qdrant, MinIO). Strong testing discipline
> (700+ automated tests) and honest documentation of limitations.

## Engineering-manager-friendly summary

> Runner.ai demonstrates judgment, not just breadth. The agent runtime is layered
> additively on an existing platform with a strict one-way dependency and a
> config-free-at-import rule, so it's fully unit-testable without infrastructure —
> the entire suite runs on pytest alone. Behavior is explicit (a typed runtime
> state machine with bounded repair and durable checkpoint/resume), safety is
> deliberate (no data leakage, guarded auth, cardinality-safe metrics), and
> operations are real (health/readiness, rate limiting, disconnect-safe streaming,
> auditable deploy/rollback/backup scripts). Limitations — notably the development
> auth stub — are documented and guarded rather than hidden.
