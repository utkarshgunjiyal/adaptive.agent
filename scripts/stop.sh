#!/usr/bin/env bash
# Stop services safely (Phase 42B). Does NOT remove named volumes by default, so
# data is preserved. Pass --with-volumes to also delete data (guarded).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

if [[ "${1:-}" == "--with-volumes" ]]; then
  warn "This will DELETE all data volumes (Mongo, Qdrant, MinIO, Redis, certs)."
  confirm "Are you absolutely sure?"
  dc down --volumes
  log "Stopped and removed volumes."
else
  dc down
  log "Stopped. Data volumes preserved (use --with-volumes to remove them)."
fi
