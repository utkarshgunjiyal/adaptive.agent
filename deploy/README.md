# Deployment assets (Phase 42B)

Reverse-proxy configuration for the single-VM production topology.

## Files

- **`Caddyfile`** — the production reverse proxy. Caddy is the only public
  service; it terminates HTTPS (automatic ACME) and routes:
  - `/agent/*`, `/health/*`, `/chat/*`, `/documents/*`, `/jobs/*`, `/memory/*` → **backend**
    (SSE-safe: `flush_interval -1`, no response timeout, forwarded headers)
  - everything else → **frontend** SPA
  - `/metrics` → `404` (never public, even if the backend serves it internally)
- **`auth.conf`** — optional HTTP basic-auth imported into the site. Empty by
  default (no proxy auth). Fill it in to lock a **private demo**.

## Why Caddy (not nginx)

Caddy gives **automatic HTTPS** (fetches and renews Let's Encrypt certificates
with zero extra config), a one-line SSE-safe reverse proxy (`flush_interval -1`),
and trivial `basic_auth` for a private demo. On a single VM that removes the
manual certbot/renewal plumbing nginx would need. The frontend image still uses
nginx *internally* to serve static assets; Caddy is only the public edge.

## Certificate handling

- **Public domain**: set `DOMAIN` and `TLS_EMAIL` (in `.env`). Point the domain's
  DNS at the VM, open ports 80 and 443, and Caddy obtains + renews the cert
  automatically. Certs persist in the `runner_caddy_data` volume across restarts.
- **No public DNS** (IP-only / internal): comment `TLS_EMAIL` in `Caddyfile`;
  Caddy serves its own self-signed cert. Clients must accept it
  (`CURL_OPTS=-k` for the smoke test).

## Private-demo auth (Option C)

To password-protect a demo without changing the app:

```bash
docker run --rm caddy caddy hash-password --plaintext 'YOUR_PASSWORD'
# paste the hash into deploy/auth.conf inside a basic_auth block, then:
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec caddy \
  caddy reload --config /etc/caddy/Caddyfile
```

Basic auth gates casual access; it is **not** a substitute for real application
authentication before public multi-user use. See [../docs/SECURITY.md](../docs/SECURITY.md).

## Validate locally

```bash
docker run --rm -e DOMAIN=demo.example.com -e TLS_EMAIL=you@example.com \
  -e BACKEND_UPSTREAM=backend:8000 -e FRONTEND_UPSTREAM=frontend:80 \
  -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v "$PWD/deploy/auth.conf:/etc/caddy/auth.conf:ro" \
  caddy:2.8-alpine caddy validate --config /etc/caddy/Caddyfile
```

CI runs exactly this check.
