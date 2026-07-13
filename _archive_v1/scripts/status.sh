#!/usr/bin/env bash
# Show service status + backend readiness (Phase 42B). Read-only.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

log "Services (profile: ${PROFILE:-prod}):"
dc ps

echo >&2
log "Backend liveness / readiness (from inside the network):"
if dc exec -T backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/live').read().decode())" 2>/dev/null; then
  :
else
  warn "liveness check could not run (backend not up?)"
fi
dc exec -T backend python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://localhost:8000/health/ready'); print(r.read().decode())" 2>/dev/null \
  || warn "readiness endpoint returned non-200 (a dependency may be down)"
