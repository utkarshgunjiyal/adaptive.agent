#!/usr/bin/env bash
# Restore persistent data from a backup directory (Phase 42B). DESTRUCTIVE:
# overwrites current Mongo/Qdrant/MinIO data. Guarded and never runs by default.
#
#   ./scripts/restore.sh ./backups/<timestamp>
#
# Volume restores require the data services to be stopped for consistency; this
# script stops them, restores, and starts them back up.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_cmd docker

SRC="${1:-}"
[[ -n "$SRC" && -d "$SRC" ]] || die "usage: restore.sh <backup-dir>"
log "Restoring from $SRC"
[[ -f "$SRC/MANIFEST.txt" ]] && cat "$SRC/MANIFEST.txt" >&2 || warn "no MANIFEST.txt in $SRC"
warn "This OVERWRITES current Mongo, Qdrant and MinIO data."
confirm "Proceed with restore?"

find_volume() { docker volume ls --format '{{.Name}}' | grep -E "_$1\$" | head -1; }
untar_volume() {
  local vol="$1" archive="$2"
  [[ -f "$SRC/$archive" ]] || { warn "$archive not in backup; skipping"; return 0; }
  [[ -n "$vol" ]] || die "target volume for $archive not found (deploy once to create it)"
  docker run --rm -v "$vol":/dest -v "$SRC":/src:ro alpine sh -c \
    "rm -rf /dest/* && tar xzf /src/$archive -C /dest" && log "  restored $archive -> $vol"
}

# Mongo can restore while running (logical restore with --drop).
if [[ -f "$SRC/mongo.archive.gz" ]]; then
  dc up -d mongodb
  dc exec -T mongodb sh -c 'command -v mongorestore >/dev/null' \
    || die "mongorestore not available in the mongo container"
  dc exec -T mongodb mongorestore --archive --gzip --drop < "$SRC/mongo.archive.gz" \
    && log "  restored mongo.archive.gz"
else
  warn "no mongo.archive.gz in backup; skipping Mongo restore"
fi

# Qdrant + MinIO volume restores require the services stopped.
log "Stopping qdrant + minio for volume restore…"
dc stop qdrant minio >/dev/null 2>&1 || true
untar_volume "$(find_volume runner_qdrant_data)" "qdrant.tgz"
untar_volume "$(find_volume runner_minio_data)"  "minio.tgz"

log "Starting services back up…"
dc up -d
log "Restore complete. Verify with ./scripts/smoke-test.sh."
