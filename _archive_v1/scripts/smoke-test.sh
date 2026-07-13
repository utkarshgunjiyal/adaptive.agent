#!/usr/bin/env bash
# Deployment smoke test (Phase 42B). Exercises the PUBLIC edge and fails clearly.
# Uses only the deterministic runtime — no paid provider is ever required. HITL
# checks run only when the target is in demo mode (otherwise they are skipped
# with a clear note, never faked).
#
#   BASE_URL=https://demo.example.com ./scripts/smoke-test.sh
#   BASE_URL=http://localhost:8000    ./scripts/smoke-test.sh   # backend direct
#
# Optional: CURL_OPTS="-k" (self-signed TLS), BASIC_AUTH="user:pass" (Caddy auth).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

load_env
BASE_URL="${BASE_URL:-${DOMAIN:+https://$DOMAIN}}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"
require_cmd curl

CURL=(curl -sS --max-time 20)
if [[ -n "${CURL_OPTS:-}" ]]; then
  read -ra _curl_opts <<<"$CURL_OPTS"
  CURL+=("${_curl_opts[@]}")
fi
[[ -n "${BASIC_AUTH:-}" ]] && CURL+=(-u "$BASIC_AUTH")

PASS=0 FAIL=0 SKIP=0
pass() { printf '  PASS  %s\n' "$1" >&2; PASS=$((PASS+1)); }
fail() { printf '  FAIL  %s\n' "$1" >&2; FAIL=$((FAIL+1)); }
skip() { printf '  skip  %s\n' "$1" >&2; SKIP=$((SKIP+1)); }

json_get() { python3 -c 'import sys,json;
try: print(json.load(sys.stdin).get(sys.argv[1],""))
except Exception: print("")' "$1" 2>/dev/null || true; }

code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$@"; }

log "Smoke testing $BASE_URL"

# 1. Frontend reachable
[[ "$(code "$BASE_URL/")" == "200" ]] && pass "frontend/root reachable (200)" || fail "root not reachable"

# 2. Liveness
[[ "$(code "$BASE_URL/health/live")" == "200" ]] && pass "backend liveness 200" || fail "liveness not 200"

# 3. Readiness
rc="$(code "$BASE_URL/health/ready")"
[[ "$rc" == "200" ]] && pass "backend readiness 200" || fail "readiness $rc (a dependency is down)"

# 4. Security + correlation headers
headers="$("${CURL[@]}" -D - -o /dev/null "$BASE_URL/health/live" || true)"
grep -qi '^x-content-type-options: nosniff' <<<"$headers" && pass "security headers present" || fail "missing X-Content-Type-Options"
grep -qiE '^x-request-id:' <<<"$headers" && pass "correlation id header present" || fail "missing correlation id header"

# 5. Metrics must not be public
mc="$(code "$BASE_URL/metrics")"
[[ "$mc" == "404" || "$mc" == "401" || "$mc" == "403" ]] && pass "/metrics not public ($mc)" || fail "/metrics reachable ($mc) — must not be public"

# 6. Internal infra ports must not be public (only when targeting a remote host)
host="$(sed -E 's#^https?://##; s#/.*##; s#:.*##' <<<"$BASE_URL")"
if [[ "$host" != "localhost" && "$host" != "127.0.0.1" ]] && command -v nc >/dev/null 2>&1; then
  for port in 27017 6379 6333 9000 9001; do
    if nc -z -w3 "$host" "$port" 2>/dev/null; then fail "infra port $port is PUBLIC on $host"; else pass "infra port $port not public"; fi
  done
else
  skip "infra port exposure check (local target or nc unavailable)"
fi

# 7. Basic runtime request (deterministic — no provider key needed)
run_resp="$("${CURL[@]}" -X POST "$BASE_URL/agent/run" \
  -H 'content-type: application/json' \
  -d '{"user_request":"What does the document say about pricing?"}' || true)"
outcome="$(json_get runtime_outcome <<<"$run_resp")"
[[ -n "$outcome" ]] && pass "runtime request succeeded (outcome=$outcome)" || fail "runtime request failed: $(head -c 200 <<<"$run_resp")"

# 8. SSE endpoint returns an event stream
ct="$("${CURL[@]}" -D - -o /dev/null -X POST "$BASE_URL/agent/run/stream" \
  -H 'content-type: application/json' -H 'accept: text/event-stream' \
  -d '{"user_request":"hello"}' 2>/dev/null | grep -i '^content-type:' || true)"
grep -qi 'text/event-stream' <<<"$ct" && pass "SSE content-type is text/event-stream" || fail "SSE endpoint not an event-stream ($ct)"

# 9 + 10. HITL pause + resume (demo mode only)
hitl_resp="$("${CURL[@]}" -X POST "$BASE_URL/agent/run" \
  -H 'content-type: application/json' \
  -d '{"user_request":"Delete all archived documents for finance"}' || true)"
hitl_outcome="$(json_get runtime_outcome <<<"$hitl_resp")"
checkpoint="$(json_get checkpoint_id <<<"$hitl_resp")"
if [[ "$hitl_outcome" == "waiting_for_approval" && -n "$checkpoint" ]]; then
  pass "HITL pause returned checkpoint id"
  resume_resp="$("${CURL[@]}" -X POST "$BASE_URL/agent/resume" \
    -H 'content-type: application/json' \
    -d "{\"checkpoint_id\":\"$checkpoint\",\"resolution\":{\"kind\":\"approval\",\"reason\":\"approved by smoke test\"}}" || true)"
  ro="$(json_get runtime_outcome <<<"$resume_resp")"
  [[ -n "$ro" && "$ro" != "waiting_for_approval" ]] && pass "resume completed (outcome=$ro)" || fail "resume did not complete (outcome=$ro)"
else
  skip "HITL pause/resume (target not in demo mode; outcome=$hitl_outcome)"
fi

echo >&2
log "Smoke test: $PASS passed, $FAIL failed, $SKIP skipped"
[[ "$FAIL" -eq 0 ]] || die "smoke test FAILED"
