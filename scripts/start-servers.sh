#!/usr/bin/env bash
# Start the store (:8100), the API (:8000) and the dashboard (:5173) in the background.
# They keep running after you close this terminal (or Claude Code restarts). Logs: .local/logs/
#
#   scripts/start-servers.sh                         # AWS mode (settings from .env)
#   AGENTPASS_MODE=local scripts/start-servers.sh    # offline fallback, no AWS
#   scripts/stop-servers.sh                          # stop all three
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/.local/logs"
mkdir -p "$LOGS"
# shellcheck disable=SC1090
[ -f "$HOME/.aws_workshop_env" ] && source "$HOME/.aws_workshop_env"

up() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }

start() {  # start <name> <port> <dir> <command...>
  local name=$1 port=$2 dir=$3; shift 3
  if up "$port"; then echo "  $name: already running on :$port"; return; fi
  (cd "$dir" && nohup "$@" > "$LOGS/$name.log" 2>&1 &)
  echo "  $name: starting on :$port"
}

echo "Starting Aide-commerce (mode: ${AGENTPASS_MODE:-from .env})"
start store 8100 "$ROOT" uv run python -m store.server --backend mock --checkout mock
start api 8000 "$ROOT" uv run python -m agentpass
start dashboard 5173 "$ROOT/dashboard" npx vite --host 127.0.0.1 --port 5173

for i in $(seq 1 40); do
  if up 8100 && up 8000 && up 5173; then break; fi
  sleep 0.5
done
echo
for pair in "store:8100" "api:8000" "dashboard:5173"; do
  name=${pair%%:*}; port=${pair##*:}
  if up "$port"; then echo "  ✓ $name is up on :$port"; else echo "  ✗ $name is NOT up: see $LOGS/$name.log"; fi
done
health=$(curl -s -m 5 http://127.0.0.1:8000/health || true)
case "$health" in
  *'"mode":"aws"'*) echo "  ✓ API mode: aws" ;;
  *'"mode":"local"'*) echo "  ✓ API mode: local (sign in as parent / parent-pass)" ;;
  *) echo "  ✗ API health check failed" ;;
esac
echo
echo "Dashboard: http://127.0.0.1:5173"
