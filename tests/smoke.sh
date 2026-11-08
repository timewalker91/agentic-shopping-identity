#!/usr/bin/env bash
# Full function check, no AWS needed: starts the store and agentpass in local mode, runs every
# agent flow (login, passport, tools, happy, over_budget, hijacked, replay, curl replay, Stop,
# reset), then stops the servers. Uses .venv/bin/python if present (or $PYTHON, else python3).
set -uo pipefail
cd "$(dirname "$0")/.."
# Use the repo venv if present so the script works without `source .venv/bin/activate`
# (macOS has no bare `python`). Override with PYTHON=/path/to/python.
PY="${PYTHON:-$( [ -x .venv/bin/python ] && echo "$PWD/.venv/bin/python" || command -v python3 )}"
python() { "$PY" "$@"; }
export PYTHON="$PY"
AP_PORT="${AGENTPASS_PORT:-8000}"; ST_PORT="${STORE_PORT:-8100}"
WORK="$(mktemp -d)"
export AGENT_STATE_FILE="$WORK/agent-state.json" AGENT_USER=parent AGENT_PASSWORD=parent-pass
export AGENTPASS_API_URL="http://127.0.0.1:$AP_PORT" AGENTPASS_MODE=local
# Pin the mock demo setup: these flows assert mock data ($50 budget, $89 headphones, poisoned
# markers listing), so .env settings for a Shopify store must not leak in. Exported values win
# over .env (load_dotenv never overrides).
export STORE_BACKEND=mock STORE_CHECKOUT=mock AGENTPASS_DEMO_BUDGET=50 PAYMENT_MOCK_FUNDS=150
export AGENTPASS_DEMO_MERCHANT=freshmart STORE_MERCHANT=freshmart AGENTPASS_DEMO_SHIP_TO=addr_home
export AGENT_QUERY_HAPPY="school supplies" AGENT_QUERY_EXPENSIVE=headphones AGENT_QUERY_CHEAP=notebook AGENT_QUERY_POISONED=markers

python -m store.server --port "$ST_PORT" >"$WORK/store.log" 2>&1 & STORE_PID=$!
AGENTPASS_PORT="$AP_PORT" AGENTPASS_STORE_URL="http://127.0.0.1:$ST_PORT/mcp" python -m agentpass >"$WORK/agentpass.log" 2>&1 & AP_PID=$!
trap 'kill $STORE_PID $AP_PID 2>/dev/null; wait $STORE_PID $AP_PID 2>/dev/null' EXIT
for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$AP_PORT/health" >/dev/null && curl -sf "http://127.0.0.1:$ST_PORT/health" >/dev/null && break
  sleep 0.25
done

FAILS=0
step() { echo; echo "=== $1"; shift; "$@" || { echo "!!! step failed: $*"; FAILS=$((FAILS+1)); }; }
expect_fail() { echo; echo "=== $1 (expected to be refused)"; shift; if "$@"; then echo "!!! was NOT refused"; FAILS=$((FAILS+1)); fi; }

step "health"               curl -sf "http://127.0.0.1:$AP_PORT/health"
step "login"                python -m agent login
step "session start"        python -m agent session start
step "tools/list"           python -m agent tools
step "single tool call"     python -m agent call search_products --args '{"query":"pens"}'
step "S1 happy path"        python -m agent shop --scenario happy
step "passport + budget"    python -m agent passport
step "S2 over budget"       python -m agent shop --scenario over_budget --cart cart_ob
step "new session"          python -m agent session start
step "S3 hijacked"          python -m agent shop --scenario hijacked
step "replay -> 401"        python -m agent replay --expect 401
step "curl replay -> 401"   tests/replay_token.sh "$WORK/passport.jwt"
step "audit feed"           python -m agent audit
step "new session"          python -m agent session start
step "S6 Stop button"       python -m agent session stop
expect_fail "call after Stop" python -m agent call view_cart --args '{"cart_id":"c"}'
step "demo reset"           python -m agent demo reset
step "pytest"               python -m pytest -q --no-header -p no:cacheprovider

echo
if grep -q "Bearer" "$WORK/agentpass.log" "$WORK/store.log"; then echo "!!! a token reached the server logs"; FAILS=$((FAILS+1)); fi
if [ "$FAILS" -eq 0 ]; then echo "ALL FLOWS OK (logs in $WORK)"; else echo "$FAILS FLOW(S) FAILED (logs in $WORK)"; exit 1; fi
