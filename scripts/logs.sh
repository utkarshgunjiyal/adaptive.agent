#!/usr/bin/env bash
# Tail structured logs (Phase 42B). Read-only.
#
#   ./scripts/logs.sh                 # all services, follow
#   ./scripts/logs.sh backend         # one service, follow
#   ./scripts/logs.sh backend 500     # last 500 lines then follow
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

SERVICE="${1:-}"
TAIL="${2:-200}"
if [[ -n "$SERVICE" ]]; then
  dc logs -f --tail "$TAIL" "$SERVICE"
else
  dc logs -f --tail "$TAIL"
fi
