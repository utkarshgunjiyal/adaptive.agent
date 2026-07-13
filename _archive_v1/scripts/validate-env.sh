#!/usr/bin/env bash
# Validate the deployment environment before starting (Phase 42B).
# Loads .env (if present) and runs the config-free python validator. Never prints
# secret values. Exit non-zero on blocking errors.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

load_env

# Prefer a running/available backend image so the check matches the deployed
# code; fall back to a local python if the module is importable on the host.
if command -v python3 >/dev/null 2>&1 && python3 -c "import app.deploy.env_check" >/dev/null 2>&1; then
  ( cd "$ROOT_DIR/backend" && python3 -m app.deploy.env_check )
elif command -v docker >/dev/null 2>&1; then
  log "Running env validation inside the backend image…"
  # Pass the current environment through; the validator reads os.environ.
  dc run --rm --no-deps -T backend python -m app.deploy.env_check
else
  die "need either python3 with the backend on PYTHONPATH, or docker, to validate"
fi
