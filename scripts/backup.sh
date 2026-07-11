#!/usr/bin/env bash
# Back up persistent data (Phase 42B): MongoDB (logical dump), Qdrant + MinIO
# (volume snapshots), plus a manifest. Does NOT back up secrets — .env must be
# backed up separately/encrypted by the operator. Non-destructive.
#
#   ./scripts/backup.sh                 # -> ./backups/<UTC-timestamp>/
#   BACKUP_DIR=/mnt/backups ./scripts/backup.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
require_cmd docker

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR:-$ROOT_DIR/backups}/$STAMP"
mkdir -p "$DEST"
log "Backing up to $DEST"

find_volume() { docker volume ls --format '{{.Name}}' | grep -E "_$1\$" | head -1; }
tar_volume() {
  local vol="$1" out="$2"
  [[ -n "$vol" ]] || { warn "volume for $out not found; skipping"; return 0; }
  docker run --rm -v "$vol":/src:ro -v "$DEST":/dest alpine \
    tar czf "/dest/$(basename "$out")" -C /src . && log "  saved $out from $vol"
}

# MongoDB — consistent logical dump (checkpoints + app collections).
if dc exec -T mongodb sh -c 'command -v mongodump >/dev/null'; then
  dc exec -T mongodb mongodump --archive --gzip > "$DEST/mongo.archive.gz" \
    && log "  saved mongo.archive.gz"
else
  warn "mongodump not available in the mongo container; skipping Mongo dump"
fi

# Qdrant + MinIO — volume snapshots (engine-agnostic, no in-container client).
tar_volume "$(find_volume runner_qdrant_data)" "qdrant.tgz"
tar_volume "$(find_volume runner_minio_data)"  "minio.tgz"

# Redis is treated as ephemeral (job queue / cache); durable state lives in
# Mongo. It is intentionally not backed up. See docs/BACKUP_RESTORE.md.

# Manifest (no secrets).
{
  echo "timestamp_utc: $STAMP"
  echo "profile: ${PROFILE:-prod}"
  if git -C "$ROOT_DIR" rev-parse HEAD >/dev/null 2>&1; then
    echo "commit: $(git -C "$ROOT_DIR" rev-parse HEAD)"
  fi
  echo "contents: mongo.archive.gz qdrant.tgz minio.tgz"
} > "$DEST/MANIFEST.txt"

log "Backup complete: $DEST"
warn "Remember: back up your .env (secrets) separately and ENCRYPTED — it is not included here."
