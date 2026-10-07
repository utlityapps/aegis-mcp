#!/usr/bin/env bash
# Start Aegis for the demo: server in the background, health checks, then the voice simulator's address.
#
#   scripts/demo_up.sh            # port 8766 by default (AEGIS_PORT overrides)
#   scripts/demo_down.sh          # stop it
#
# Every check is read-only, so the demo state stays fresh for recording.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
PORT="${AEGIS_PORT:-8766}"
BASE="http://localhost:$PORT"
LOGS="$ROOT/logs"
PID_FILE="$LOGS/aegis-demo.pid"

fail() { printf '\n  ✗ %s\n\n' "$1" >&2; exit 1; }
ok() { printf '  ✓ %s\n' "$1"; }

# --- 1. dependencies -----------------------------------------------------------------------------
[[ -x "$PY" ]] || fail "No .venv. Create it: python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
"$PY" -c 'import sys; assert sys.version_info >= (3, 12)' || fail ".venv must use Python 3.12+"
"$PY" -c 'import mcp, uvicorn, starlette, jsonschema, opentelemetry, aegis, server' 2>/dev/null \
  || fail "Dependencies missing. Run: .venv/bin/pip install -e '.[dev]'"
command -v curl >/dev/null || fail "curl is required"

# --- 2. server -----------------------------------------------------------------------------------
mkdir -p "$LOGS"
running_pid=""
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  running_pid="$(cat "$PID_FILE")"
fi
if [[ -n "$running_pid" ]]; then
  ok "Aegis already running (pid $running_pid) — reusing it"
elif lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port $PORT is in use by another process: $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | awk 'NR==2{print $1" pid "$2}')"
else
  env -u AEGIS_WEBHOOK_SECRET -u AEGIS_DISPLAY_TOKEN AEGIS_PORT="$PORT" \
    nohup "$PY" -m server >"$LOGS/aegis-metrics.log" 2>"$LOGS/aegis-server.log" </dev/null &
  echo $! > "$PID_FILE"
  ok "Started Aegis (pid $(cat "$PID_FILE")), logs in logs/"
fi

rpc() {
  curl -s --max-time 3 "$BASE/mcp" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d "$1"
}
init() {
  rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"$1\",\"capabilities\":{},\"clientInfo\":{\"name\":\"demo-check\",\"version\":\"1\"}}}"
}
for _ in $(seq 1 30); do
  init 2025-11-25 | grep -q '"name":"aegis"' && break
  sleep 0.5
done
init 2025-11-25 | grep -q '"name":"aegis"' || fail "Server did not come up — see logs/aegis-server.log"

# --- 3. health checks (read-only) ----------------------------------------------------------------
echo
echo "  Health checks"
init 2025-11-25 | grep -q '"protocolVersion":"2025-11-25"' && ok "initialize 2025-11-25 (hackathon minimum) → negotiated 2025-11-25" \
  || fail "2025-11-25 handshake failed"
init 2025-03-26 | grep -q '"protocolVersion":"2025-03-26"' && ok "initialize 2025-03-26 (what Alexa+ documents sending) → accepted" \
  || fail "2025-03-26 handshake failed"
tools="$(rpc '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | "$PY" -c 'import json,sys; print(",".join(t["name"] for t in json.load(sys.stdin)["result"]["tools"]))')"
[[ "$tools" == "list_voicemails,check_voicemail,explain_red_flags,block_number,report_scam" ]] \
  && ok "tools/list → 5 tools: ${tools//,/, }" || fail "unexpected tools: $tools"
verdict="$(rpc '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"check_voicemail","arguments":{"caller_hint":"IRS"}}}' \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["result"]["structuredContent"]["verdict"])')"
[[ "$verdict" == "SCAM" ]] && ok "check_voicemail(\"IRS\") → SCAM (read-only, no state changed)" || fail "demo voicemail check returned $verdict"
curl -s --max-time 3 "$BASE/simulator" | grep -q 'Aegis Voice Simulator' \
  && ok "voice simulator page served at $BASE/simulator" || fail "voice simulator page not served"

# --- 4. telemetry --------------------------------------------------------------------------------
sleep 0.5  # let the background log thread flush
emf_lines="$("$PY" - "$LOGS/aegis-metrics.log" <<'PYEOF'
import json, sys
count = 0
for line in open(sys.argv[1], encoding="utf-8"):
    doc = json.loads(line)  # raises if anything but EMF reached stdout
    assert doc["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Aegis"
    count += 1
print(count)
PYEOF
)" || fail "logs/aegis-metrics.log contains non-EMF output"
[[ "$emf_lines" -gt 0 ]] || fail "No EMF metric lines yet"
ok "Telemetry live: $emf_lines EMF metric lines in logs/aegis-metrics.log, all valid"

cat <<BANNER

  ╔══════════════════════════════════════════════════════════════════════╗
  ║                             AEGIS READY                               ║
  ╚══════════════════════════════════════════════════════════════════════╝
    Server      pid $(cat "$PID_FILE" 2>/dev/null || echo "$running_pid") · $BASE/mcp (stateless Streamable HTTP)
    Simulator   http://127.0.0.1:$PORT  (web MCP client: open it in Safari or Chrome)
    Telemetry   logs/aegis-metrics.log (EMF) · logs/aegis-server.log (redacted)

  Recording: docs/RECORDING_GUIDE.md · Between takes: scripts/demo_down.sh && scripts/demo_up.sh
BANNER
