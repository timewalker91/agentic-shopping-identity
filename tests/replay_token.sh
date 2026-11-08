#!/usr/bin/env bash
# Token replay (A10 step 4): copy the agent's unexpired passport and replay it with curl.
# After Stop or auto-revoke the gateway must answer HTTP 401 with reason session_revoked.
#
#   tests/replay_token.sh            # exports the current token from the agent state, then replays it
#   tests/replay_token.sh TOKENFILE  # replays a token you copied earlier
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$( [ -x "$ROOT/.venv/bin/python" ] && echo "$ROOT/.venv/bin/python" || command -v python3 )}"
python() { "$PY" "$@"; }
API="${AGENTPASS_API_URL:-http://127.0.0.1:8000}"
TOKEN_FILE="${1:-${TMPDIR:-/tmp}/agentpass-passport.jwt}"
if [ ! -s "$TOKEN_FILE" ]; then
  python -m agent token --out "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"
BODY='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"view_cart","arguments":{"cart_id":"cart_replay"}}}'
STATUS="$(curl -s -o "${TOKEN_FILE}.response" -w '%{http_code}' -X POST "$API/mcp" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$BODY")"
echo "HTTP $STATUS"
cat "${TOKEN_FILE}.response"; echo
if [ "$STATUS" = "401" ]; then echo "replay blocked (expected after revoke)"; else echo "replay was NOT blocked"; exit 1; fi
