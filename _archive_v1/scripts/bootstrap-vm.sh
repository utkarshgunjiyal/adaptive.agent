#!/usr/bin/env bash
# Install Docker Engine + Compose plugin on a fresh Ubuntu VM (Phase 42B).
# Idempotent: safe to re-run. Requires sudo. Does NOT deploy anything.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

[[ -r /etc/os-release ]] || die "cannot read /etc/os-release (Ubuntu/Debian expected)"
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) die "this bootstrap targets Ubuntu/Debian; detected '${ID:-unknown}'. Install Docker manually." ;;
esac

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "Docker Engine + Compose already installed:"
  docker --version >&2
  docker compose version >&2
  exit 0
fi

SUDO=""
[[ "$(id -u)" -eq 0 ]] || SUDO="sudo"
command -v "$SUDO" >/dev/null 2>&1 || [[ -z "$SUDO" ]] || die "sudo not available; run as root"

log "Installing Docker Engine from the official Docker apt repository…"
$SUDO apt-get update
$SUDO apt-get install -y ca-certificates curl gnupg
$SUDO install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
  | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
$SUDO apt-get update
$SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

$SUDO systemctl enable --now docker

# Allow the invoking user to run docker without sudo (takes effect on next login).
if [[ -n "${SUDO_USER:-}" ]]; then
  $SUDO usermod -aG docker "$SUDO_USER" || warn "could not add $SUDO_USER to the docker group"
  warn "log out and back in for docker group membership to take effect"
fi

log "Done. Versions:"
docker --version >&2
docker compose version >&2
