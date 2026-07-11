# Video Demo Script (5–7 minutes)

A shot-by-shot script for recording the Runner.ai demo. Run in **demo mode**
(deterministic, offline) so nothing depends on a live provider. Total ≈ 6 min.

**Before recording**
- Start: `DEMO_MODE=true ENVIRONMENT=demo` backend + `npm run dev` frontend (see
  [DEMO.md](./DEMO.md)); confirm `./scripts/smoke-test.sh` is green.
- Have two terminals ready: (1) frontend/app, (2) `./scripts/logs.sh backend`.
- Zoom the browser to ~125%. Hide bookmarks, notifications, and any `.env`.
- **Do not show:** `.env`, API keys, tokens, MinIO credentials, or noisy stack
  traces. Keep the logs terminal filtered to structured lines.

---

## 1 · Problem — 30s

**On screen:** the app's empty chat.

**Narration:**
> "This is Runner.ai — an autonomous AI agent, not a chatbot. Most 'AI apps' map a
> prompt to text. Runner.ai routes each request, plans, uses tools, grounds its
> answer in retrieved evidence, and — when it isn't sure — pauses and asks a human,
> then resumes exactly where it left off. Let me show you."

## 2 · Architecture overview — 60s

**On screen:** the topology diagram from ARCHITECTURE_WALKTHROUGH.md (or a slide).

**Narration:**
> "The shape: a request enters through a reverse proxy over HTTPS. A deterministic
> Behavior Gate decides — *before* any LLM call — whether it needs planning. Context
> is built under a token budget from conversation, memory, and vector search. Tools
> come from one unified registry — internal adapters and external MCP servers look
> identical to the planner. Tools return *evidence*; the model synthesizes the
> answer, streamed token-by-token. An evaluator can trigger bounded repair or a
> human-in-the-loop pause backed by a durable checkpoint. The agent layer is
> config-free and one-way-dependent on the platform, so it's fully unit-testable —
> over 700 tests, no database required."

## 3 · Successful live run — 90s

**UI actions:**
1. Type: `What does the document say about pricing?` → **Send**.
2. Point at the **runtime timeline** as events appear.
3. Point at the **streaming answer** as tokens arrive.

**Narration:**
> "I'll ask a normal question. Watch the runtime timeline on the right: the run
> starts, context and routing happen, a capability is retrieved and executed —
> here's the tool execution card — and then the answer streams in token-by-token
> over SSE. These aren't fake UI events; each one is a real RuntimeEvent from the
> backend. The final outcome is 'completed'."

**Cutaway (terminal 2):** show one structured log line.
> "And every request carries a correlation id — I can trace this exact request
> across services by its request id."

## 4 · HITL pause / resume — 90s

**UI actions:**
1. Type: `Delete all archived documents for finance` → **Send**.
2. Timeline ends in **waiting_for_approval**; the **ApprovalPanel** appears.
3. Point at the checkpoint indicator.
4. Click **Approve**.
5. Show the run **resume and complete**.

**Narration:**
> "Now a high-impact request. The agent recognizes this needs human approval, so
> instead of acting it **pauses** — the run is checkpointed and returns a checkpoint
> id. The UI shows an approval panel. This is a genuine suspend: the run is durable.
> I click Approve — the client calls /agent/resume with my decision, and the **same
> run** continues from the checkpoint and completes. Same run id, folded resolution,
> real resume — not a new request pretending to continue."

**Optional:** repeat briefly with `Summarize the report` → clarification pause →
type a clarification → resumes. (Skip if tight on time.)

## 5 · Production engineering — 60s

**On screen:** split — terminal running `./scripts/smoke-test.sh` and the
`docker-compose.prod.yml` / `deploy/Caddyfile` open.

**Narration:**
> "This isn't just a notebook. It's containerized and deploys to a single VM with
> Caddy terminating HTTPS as the only public service — backend, frontend, and all
> infrastructure stay internal. There are health and readiness endpoints, injectable
> metrics with a guard that drops high-cardinality and PII labels, per-route rate
> limiting backed by Redis, and SSE that cancels background work when a client
> disconnects. Deployment is scripted — validate-env, deploy, rollback, backup and
> restore — and a smoke test verifies the whole thing, including that internal
> services and /metrics are never public."

**On screen:** show `./scripts/smoke-test.sh` output ending in "passed".

## 6 · Trade-offs and conclusion — 30–60s

**On screen:** the INTERVIEW_GUIDE limitations section, or back to the app.

**Narration:**
> "Honest trade-offs: it's a single VM today — the seams for horizontal scale are
> there (externalized state, a distributed rate limiter) but not wired. And the
> shipped authentication is a development stub — there's a startup guard that
> *refuses* to run it silently in production, but real auth is the first thing I'd
> add for public use. What I'm proud of: deterministic routing before the LLM, a
> planner/executor split with tools as evidence, and durable checkpoint/resume for
> real human-in-the-loop control — all in a runtime that's small, explicit, and
> fully testable. Thanks for watching."

---

## Fallback plan if a live provider fails

The entire script runs in **demo mode**, which uses the deterministic provider and
needs **no external calls** — so a provider or network outage does not affect it.
Keep a pre-recorded capture of sections 3–4 as a backup to splice in if the local
environment itself is unavailable. If anything breaks on camera, cut to the
recording rather than improvising or faking output.
