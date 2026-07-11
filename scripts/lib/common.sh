#!/usr/bin/env bash
# Shared helpers for the Runner.ai deploy scripts (Phase 42B).
# Sourced by every script; never executed directly.
set -euo pipefail

# Repo root = two levels up from scripts/lib/.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# --- logging (to stderr; never prints secret values) ----------------------- #
_c() { [[ -t 2 ]] && printf '%s' "$1" || printf ''; }
log()  { printf '%s[runner]%s %s\n' "$(_c $'\033[1;34m')" "$(_c $'\033[0m')" "$*" >&2; }
warn() { printf '%s[warn]%s %s\n'   "$(_c $'\033[1;33m')" "$(_c $'\033[0m')" "$*" >&2; }
err()  { printf '%s[error]%s %s\n'  "$(_c $'\033[1;31m')" "$(_c $'\033[0m')" "$*" >&2; }
die()  { err "$*"; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

confirm() {
  # confirm "message"  — refuses unless the user types 'yes'. Never a default yes.
  local reply
  printf '%s [type "yes" to continue]: ' "$1" >&2
  read -r reply
  [[ "$reply" == "yes" ]] || die "aborted by user"
}

# --- compose file selection via PROFILE (prod default | demo | dev) -------- #
compose_args() {
  local profile="${PROFILE:-prod}"
  COMPOSE_ARGS=(-f "$ROOT_DIR/docker-compose.yml")
  case "$profile" in
    dev)  ;;
    prod) COMPOSE_ARGS+=(-f "$ROOT_DIR/docker-compose.prod.yml") ;;
    demo) COMPOSE_ARGS+=(-f "$ROOT_DIR/docker-compose.prod.yml" -f "$ROOT_DIR/docker-compose.demo.yml") ;;
    *)    die "unknown PROFILE: $profile (use dev|prod|demo)" ;;
  esac
}

dc() {
  require_cmd docker
  compose_args
  docker compose "${COMPOSE_ARGS[@]}" "$@"
}

# --- .env loading (for scripts that need DOMAIN etc.) ---------------------- #
load_env() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT_DIR/.env"
    set +a
  fi
}

# Project name used to derive named-volume names for volume-level backup.
project_name() {
  printf '%s' "${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-')}"
}
