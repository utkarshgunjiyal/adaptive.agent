#!/usr/bin/env bash
# Update to the latest code and redeploy (Phase 42B). Fetches the tracked branch,
# fast-forwards, records the previous commit for rollback, rebuilds, and restarts.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

require_cmd git
git -C "$ROOT_DIR" rev-parse --git-dir >/dev/null 2>&1 || die "not a git checkout: $ROOT_DIR"

BRANCH="${DEPLOY_BRANCH:-$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD)}"
PREV="$(git -C "$ROOT_DIR" rev-parse HEAD)"
echo "$PREV" > "$ROOT_DIR/.previous_commit"
log "Current commit $PREV (saved for rollback). Updating branch '$BRANCH'…"

git -C "$ROOT_DIR" fetch --prune origin "$BRANCH"
git -C "$ROOT_DIR" merge --ff-only "origin/$BRANCH" || die "fast-forward failed; resolve manually (no automatic reset)"

NEW="$(git -C "$ROOT_DIR" rev-parse HEAD)"
if [[ "$PREV" == "$NEW" ]]; then
  log "Already up to date ($NEW). Nothing to redeploy."
  exit 0
fi
log "Updated $PREV -> $NEW. Redeploying…"
PROFILE="${PROFILE:-prod}" "$ROOT_DIR/scripts/deploy.sh"
