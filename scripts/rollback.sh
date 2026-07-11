#!/usr/bin/env bash
# Roll back to a previous commit and redeploy (Phase 42B).
#
#   ./scripts/rollback.sh                 # roll back to .previous_commit
#   ./scripts/rollback.sh <git-ref>       # roll back to an explicit commit/tag
#
# Named Docker volumes persist across the rollback (data is not touched).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

require_cmd git
git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1 || die "not a git checkout: $ROOT_DIR"

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  [[ -f "$ROOT_DIR/.previous_commit" ]] || die "no target given and no .previous_commit found"
  TARGET="$(cat "$ROOT_DIR/.previous_commit")"
fi
git -C "$ROOT_DIR" rev-parse --verify "$TARGET^{commit}" >/dev/null 2>&1 || die "unknown git ref: $TARGET"

CURRENT="$(git -C "$ROOT_DIR" rev-parse HEAD)"
log "Rolling back from $CURRENT to $TARGET"
confirm "This will check out $TARGET and rebuild. Data volumes are preserved. Proceed?"

git -C "$ROOT_DIR" checkout --quiet "$TARGET"
PROFILE="${PROFILE:-prod}" "$ROOT_DIR/scripts/deploy.sh"
log "Rolled back to $TARGET. (Detached HEAD — checkout your branch when ready.)"
