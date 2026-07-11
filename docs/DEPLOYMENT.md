# Deployment

> Phase 42A provides production-capable builds and composition; **Phase 42B**
> adds the single-VM topology (Caddy reverse proxy + HTTPS), deploy scripts,
> environment validation, and smoke tests. This repository is **deploy-ready**,
> but no public deployment is running unless you have performed one — the docs do
> not claim a live site.

## Prerequisites

- Docker + Docker Compose v2 (`docker compose version`), or
- Python 3.11 + Node 22 for a host run.

## 1. Local (host) run

```bash
cp .env.example .env            # fill in values (LLM key optional; stub works offline)

# Backend
cd backend
pip install -r requirements.txt && pip install pytest
uvicorn app.main:app --reload --port 8000

# Frontend (separate shell)
cd frontend
npm ci
npm run dev                     # http://localhost:5173 (proxies /agent to :8000)
```

## 2. Docker (local/demo stack)

```bash
cp .env.example .env            # optional; env_file is optional
docker compose up --build
```

Services & ports:

| Service   | URL                       | Notes                                   |
| --------- | ------------------------- | --------------------------------------- |
| frontend  | http://localhost:3000     | nginx SPA; proxies `/agent` to backend  |
| backend   | http://localhost:8000     | FastAPI (`/health`, `/health/ready`, …) |
| mongodb   | localhost:27017           | durable checkpoints (when enabled)      |
| redis     | localhost:6379            | job queue + rate limiting               |
| qdrant    | localhost:6333            | vector store                            |
| minio     | localhost:9000 / :9001    | object storage + console                |

`minio-init` creates the uploads bucket and exits.

## 3. Production-like composition

```bash
export CORS_ORIGINS=https://app.example.com
export MINIO_ROOT_USER=... MINIO_ROOT_PASSWORD=...
export ANTHROPIC_API_KEY=...            # or OPENROUTER_API_KEY
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

The prod override:
- keeps infra ports off the host (internal network only),
- enables rate limiting (Redis), metrics, Mongo checkpoints, real LLM,
- `restart: always`,
- requires `CORS_ORIGINS` (fails fast if unset).

**Before public exposure** you must also (see [SECURITY.md](./SECURITY.md)):
replace the dev auth stub with real authentication, front the stack with TLS,
and rotate all default credentials.

## 4. Single-VM production deployment (Phase 42B)

Target: one Ubuntu VM running Docker Engine + Compose, with **Caddy** as the only
public service (ports 80/443, automatic HTTPS). Backend, frontend, and all
infrastructure stay on the internal Docker network.

```
Internet ──443──▶ Caddy ──┬─▶ frontend:80        (SPA)
                          └─▶ backend:8000        (/agent, /health, SSE)
                              backend ─▶ mongodb / redis / qdrant / minio  (internal)
```

### Steps

```bash
# 0. One-time: install Docker Engine + Compose on the VM
./scripts/bootstrap-vm.sh                       # Ubuntu/Debian, idempotent

# 1. Configure
cp .env.example .env                            # set DOMAIN, TLS_EMAIL,
                                                #   CORS_ORIGINS=https://$DOMAIN,
                                                #   MINIO_ROOT_USER/PASSWORD,
                                                #   ANTHROPIC_API_KEY (or OPENROUTER_API_KEY)
./scripts/validate-env.sh                       # rejects placeholders/defaults; no secrets printed

# 2. Deploy (production profile)
PROFILE=prod ./scripts/deploy.sh                # validate → build → up → wait for health

# 3. Verify
BASE_URL=https://$DOMAIN ./scripts/smoke-test.sh
```

Point `DOMAIN`'s DNS A/AAAA record at the VM first so Caddy can complete the ACME
challenge. For a VM without a public DNS name, comment `TLS_EMAIL` in
`deploy/Caddyfile` and Caddy serves an internal self-signed cert (use
`CURL_OPTS=-k` with the smoke test).

**Auth gate (important).** With `ENVIRONMENT=production` the backend **refuses to
start** while the development auth stub is active, unless `ALLOW_DEV_AUTH=true`
is set. Either wire real authentication (a `get_current_user` dependency
override) for public use, or run the **private demo** profile behind Caddy basic
auth (see [DEMO.md](./DEMO.md) and [SECURITY.md](./SECURITY.md)).

### Operational scripts (`scripts/`)

| Script                | Purpose                                                     |
| --------------------- | ----------------------------------------------------------- |
| `bootstrap-vm.sh`     | Install Docker Engine + Compose (Ubuntu/Debian, idempotent) |
| `validate-env.sh`     | Validate env before deploy (no secrets printed)             |
| `deploy.sh`           | Validate → build → up → wait for backend health             |
| `update.sh`           | Fast-forward the branch and redeploy (saves previous commit)|
| `rollback.sh [ref]`   | Roll back to a commit/tag and redeploy (data preserved)     |
| `status.sh`           | Service status + liveness/readiness                         |
| `logs.sh [svc] [n]`   | Tail structured logs                                        |
| `smoke-test.sh`       | Post-deploy verification (fails clearly)                    |
| `backup.sh`           | Back up Mongo + Qdrant + MinIO (see [BACKUP_RESTORE.md](./BACKUP_RESTORE.md)) |
| `restore.sh <dir>`    | Restore from a backup (guarded, destructive)                |
| `stop.sh`             | Stop services (data volumes preserved)                      |

All scripts use `set -euo pipefail`, have non-destructive defaults, never print
secrets, and select the compose profile via `PROFILE=dev|prod|demo`.

## Images

- **backend** (`backend/Dockerfile`): `python:3.11-slim`, non-root (`uid 10001`),
  `tini` init for graceful shutdown, `HEALTHCHECK` on `/health/live`,
  `uvicorn --proxy-headers --timeout-graceful-shutdown 20`.
- **frontend** (`frontend/Dockerfile`): multi-stage `node:22` build → `nginx:1.27`
  static serve, SPA fallback, `/agent` reverse-proxy (buffering off for SSE),
  backend URL set at runtime via `BACKEND_URL`.

Build only:

```bash
docker compose build
```

## Health checks (for load balancers / orchestrators)

- **Liveness**: `GET /health/live` → `200 {"status":"alive"}` (no dependencies).
- **Readiness**: `GET /health/ready` → `200` when Mongo/Redis/Qdrant/MinIO are
  reachable, else `503`. Use readiness to gate traffic.

## Rollback

Images are tagged (`runner-ai-backend`, `runner-ai-frontend`). To roll back:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d \
  --no-build backend@sha256:<previous-digest>
```

or re-deploy the previous git tag and rebuild. State lives in the named volumes
(`runner_mongo_data`, …) and survives image rollbacks; a schema-incompatible
rollback should restore a Mongo snapshot first. Checkpoints are forward-only —
a rollback does not need to migrate them (waiting runs simply expire).
