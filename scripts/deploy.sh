#!/usr/bin/env bash
# Build and start the stack (Phase 42B). Validates the environment first, records
# the deployed commit, then brings services up and waits for backend health.
#
#   PROFILE=prod ./scripts/deploy.sh          # production (default)
#   PROFILE=demo ./scripts/deploy.sh          # private interview demo
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

load_env

log "Validating environment (profile: ${PROFILE:-prod})…"
"$ROOT_DIR/scripts/validate-env.sh"

# Record the commit we are deploying so rollback has a target.
if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$ROOT_DIR" rev-parse HEAD > "$ROOT_DIR/.deployed_commit"
  log "Deploying commit $(cat "$ROOT_DIR/.deployed_commit")"
fi

log "Building images…"
dc build

log "Starting services…"
dc up -d

log "Waiting for backend to become healthy…"
for _ in $(seq 1 30); do
  status="$(dc ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$1=="backend"{print $2}')"
  [[ "$status" == "healthy" ]] && { log "backend healthy"; break; }
  sleep 5
done
[[ "${status:-}" == "healthy" ]] || warn "backend not reporting healthy yet; check ./scripts/status.sh"

log "Deploy complete. Run ./scripts/smoke-test.sh to verify."
