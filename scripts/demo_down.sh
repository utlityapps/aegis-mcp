#!/usr/bin/env bash
# Stop the Aegis demo server started by scripts/demo_up.sh. Logs and .env.demo are kept.
set -euo pipefail

cd "$(dirname "$0")/.."
PID_FILE="logs/aegis-demo.pid"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
  echo "  ✓ Stopped Aegis (pid $pid)"
else
  echo "  • Aegis demo server is not running"
fi
rm -f "$PID_FILE"
