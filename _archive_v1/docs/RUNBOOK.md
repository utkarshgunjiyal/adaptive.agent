# Runbook

First-response guide for common operational situations. All commands assume the
Docker composition; adapt paths for a host run.

## Quick triage

```bash
docker compose ps                                  # what's up / restarting
curl -fsS localhost:8000/health/ready | jq         # which dependency is down
docker compose logs --tail=100 backend | jq -R 'fromjson? // .'   # structured logs
```

Filter one request across services by its correlation id:

```bash
docker compose logs backend | grep '"request_id":"<id>"'
```

## Symptom → action

### Backend returns 503 on `/health/ready`
A dependency is unreachable. The body names it (`{"dependencies":{"redis":"unavailable"}}`).
- Check that service: `docker compose ps <svc>`, `docker compose logs <svc>`.
- Restart it: `docker compose restart <svc>`.
- Liveness (`/health/live`) staying 200 means the process itself is fine.

### Clients get `429 Too Many Requests`
Rate limiting is working. Check `Retry-After`. If limits are too tight, raise
`RATE_LIMIT_{RUN,STREAM,RESUME}_PER_MINUTE` and restart the backend. Confirm
`RATE_LIMIT_BACKEND=redis` in multi-replica deployments (memory is per-process).

### SSE stream stalls / disconnects through a proxy
Ensure proxy buffering is **off** for `/agent/run/stream` (the bundled nginx
config does this). Heartbeats every `SSE_HEARTBEAT_SECONDS` keep idle streams
open; lower it if an aggressive proxy still closes them.

### A run "hangs" / high CPU after clients leave
Disconnects cancel background work automatically (Phase 42A). If you still see
orphaned load, confirm you are on this build and that the proxy actually closes
the upstream connection on client disconnect.

### LLM answers look like `[stub-llm] …`
No LLM key configured → the stub provider is active. Set `ANTHROPIC_API_KEY`
(or `OPENROUTER_API_KEY`) and `AGENT_USE_REAL_LLM=true`, then restart. **Never**
add LLM calls to health checks.

### Resume returns 404 / 409
- `404`: checkpoint unknown/expired → the run can no longer be resumed (start a
  new run). Ensure `AGENT_CHECKPOINT_BACKEND=mongo` for durability across restarts.
- `409`: the checkpoint was already resumed/cancelled (or a concurrent resume).
  The UI clears the checkpoint on 409 — expected.

### Mongo checkpoint growth
Waiting checkpoints accumulate in the `agent_checkpoints` collection. Add a TTL
index / periodic cleanup for old `resumed`/`cancelled` records if needed.

## Deployment & recovery procedures (Phase 42B)

All commands assume the repo checkout on the VM. Scripts select the profile via
`PROFILE=prod|demo` and never print secrets.

### First deployment
```bash
./scripts/bootstrap-vm.sh                     # install Docker (Ubuntu, idempotent)
cp .env.example .env && $EDITOR .env          # DOMAIN, TLS_EMAIL, CORS_ORIGINS, creds, LLM key
./scripts/validate-env.sh                     # fix any ERRORs
PROFILE=prod ./scripts/deploy.sh
BASE_URL=https://$DOMAIN ./scripts/smoke-test.sh
```

### Normal update
```bash
PROFILE=prod ./scripts/update.sh              # ff branch, save previous commit, redeploy
```

### Rollback
```bash
./scripts/rollback.sh                         # to the saved previous commit
./scripts/rollback.sh <git-sha-or-tag>        # to a specific version (data preserved)
```

### Service health check
```bash
./scripts/status.sh
curl -fsS https://$DOMAIN/health/ready | jq
```

### Service restart
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
```

### Log inspection / correlation-id tracing
```bash
./scripts/logs.sh backend 500
docker compose logs backend | grep '"request_id":"<id>"'    # one request across the stack
```

### Disk usage
```bash
df -h ; docker system df
docker image prune -f                         # reclaim space from old image layers
```

### Docker volume inspection
```bash
docker volume ls | grep runner
docker run --rm -v <project>_runner_mongo_data:/d:ro alpine du -sh /d
```

### Certificate problems
- Symptom: browser TLS warning, or Caddy logs show ACME failure.
- Check: `./scripts/logs.sh caddy 200`. Ensure DNS for `$DOMAIN` points at the VM
  and ports 80+443 are open (ACME HTTP-01 needs port 80).
- Certs persist in `runner_caddy_data`; a restart re-uses them. Force re-issue by
  removing that volume (last resort, may hit ACME rate limits).
- No public DNS? Comment `TLS_EMAIL` in `deploy/Caddyfile` for a self-signed cert.

### Provider unavailable
`AGENT_USE_REAL_LLM=true` but the LLM API is down → runs surface a safe `failed`
or `waiting_for_user` outcome (never a stack trace). Check the key/quota; the
deterministic stub (`AGENT_USE_REAL_LLM=false`) keeps the app usable offline.
**Never** add provider calls to health checks.

### MCP unavailable
MCP is **off by default** (`AGENT_MCP_ENABLED=false`) → the runtime is internal-
only and unaffected. When enabled, a failed MCP server is isolated behind the
connection manager (reconnect/health); the internal capabilities still work. Check
`./scripts/logs.sh backend` for `app.mcp_*` lines. Disable MCP to fully isolate.

### Backup / restore
```bash
./scripts/backup.sh                           # Mongo + Qdrant + MinIO -> ./backups/<ts>/
./scripts/restore.sh ./backups/<ts>           # guarded, destructive
```
See [BACKUP_RESTORE.md](./BACKUP_RESTORE.md). Back up `.env` separately + encrypted.

### Secret rotation
1. Update the secret in `.env` (e.g. `MINIO_ROOT_PASSWORD`, `ANTHROPIC_API_KEY`).
2. `./scripts/validate-env.sh` → `PROFILE=prod ./scripts/deploy.sh` (recreates the
   affected containers with the new value). For MinIO root credential changes,
   follow MinIO's rotation guidance if data already exists.
3. Rotate the Caddy basic-auth hash in `deploy/auth.conf` and
   `caddy reload` if used.

## Escalation

Capture before escalating: `docker compose ps`, the failing request's
`request_id` and the surrounding structured logs, `/health/ready` output, and
(if metrics enabled) a `/metrics` snapshot. These contain no secrets.
