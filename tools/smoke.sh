#!/usr/bin/env bash
# SPEC v1 API smoke test against any one device.
#
# Usage:
#   tools/smoke.sh <BASE_URL> [PASSWORD] [USERNAME]
#
# PASSWORD may also be supplied via the MIBEE_WEBUI_PASSWORD env var —
# keep real credentials out of shell history and command examples.
#
# Examples:
#   tools/smoke.sh http://<rs-device-ip>:8088 "$MIBEE_WEBUI_PASSWORD"   # mibee-eye-rs
#   tools/smoke.sh http://<go-device-ip>:8088 "$MIBEE_WEBUI_PASSWORD"   # mibee-eye-go
#   tools/smoke.sh https://127.0.0.1:8443 "$MIBEE_WEBUI_PASSWORD" admin # mibee-eye-notebook (TLS, self-signed)
#
# No dependencies beyond curl + a cookie jar in ${TMPDIR}. Exit 0 = all pass.
set -uo pipefail

BASE=${1:?usage: smoke.sh <BASE_URL> [PASSWORD] [USERNAME]}
PW=${2:-${MIBEE_WEBUI_PASSWORD:-}}
USER=${3:-admin}
CURL="curl -sk -m 8"
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

pass=0; fail=0
ok()   { pass=$((pass+1)); printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { fail=$((fail+1)); printf '  \033[31mFAIL\033[0m %s\n' "$1"; }
check(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (want $3, got $2)"; fi; }
contains(){ if echo "$2" | grep -Eq "$3"; then ok "$1"; else bad "$1 (missing '$3' in: $(echo "$2" | head -c 120))"; fi; }

echo "== $BASE =="

# 1. health (public)
r=$($CURL -w '|%{http_code}' "$BASE/api/health"); body=${r%|*}; code=${r##*|}
check "GET /api/health" "$code" 200
contains "  health envelope" "$body" '"ok":\s*true'

# 2. me before login → 401 (login mode) or 503 (first-boot setup mode)
code=$($CURL -o /dev/null -w '%{http_code}' "$BASE/api/auth/me")
if [ "$code" = 503 ]; then
  ok "GET /api/auth/me pre-auth (503 setup_required — first boot)"
  if [ -z "$PW" ]; then bad "need PASSWORD to run setup"; echo "result: $pass passed, $fail failed"; exit 1; fi
  code=$($CURL -c "$JAR" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/setup" \
    -H 'Content-Type: application/json' -d "{\"username\":\"$USER\",\"password\":\"$PW\"}")
  check "POST /api/auth/setup" "$code" 200
else
  check "GET /api/auth/me pre-auth" "$code" 401
  if [ -z "$PW" ]; then bad "need PASSWORD to test login"; echo "result: $pass passed, $fail failed"; exit 1; fi
  code=$($CURL -c "$JAR" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' -d "{\"username\":\"$USER\",\"password\":\"$PW\"}")
  check "POST /api/auth/login" "$code" 200
fi

# 3. me after login
body=$($CURL -b "$JAR" "$BASE/api/auth/me")
contains "GET /api/auth/me signed-in" "$body" '"ok":\s*true'

# 4. capabilities
body=$($CURL -b "$JAR" "$BASE/api/capabilities")
contains "GET /api/capabilities" "$body" 'spec_version'

# 5. cameras
code=$($CURL -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/cameras")
check "GET /api/cameras" "$code" 200

# 6. config round-trip (GET only — PUT would touch device state)
code=$($CURL -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/config")
check "GET /api/config" "$code" 200

# 7. SSE probe (server should start the event stream)
code=$($CURL -b "$JAR" -o /dev/null -w '%{http_code}' --max-time 2 "$BASE/api/events" 2>/dev/null)
[ "$code" = 200 ] && ok "GET /api/events (SSE)" || bad "GET /api/events (got $code; timeout counts as fail)"

# 8. logout → session dead
code=$($CURL -b "$JAR" -c "$JAR" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/logout")
check "POST /api/auth/logout" "$code" 204
code=$($CURL -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/api/auth/me")
check "GET /api/auth/me after logout" "$code" 401

echo "result: $pass passed, $fail failed"
exit $([ "$fail" = 0 ] && echo 0 || echo 1)
