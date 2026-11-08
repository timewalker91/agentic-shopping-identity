#!/usr/bin/env bash
# The live demo, one step per Enter: a caption, the command, then its output.
# Follows docs/demo-runbook.md section 2 (Line 1 normal flow, Line 2 revoke flow, hijack ending).
#
#   cd ~/a10hack/agentic-shopping-identity && source ~/.aws_workshop_env
#   uv run python -m agent login          # once, before you start (user parent)
#   scripts/demo.sh            # one Enter per step
#   scripts/demo.sh --auto     # hands-free: timed pauses, Stop pressed automatically (for recording)
#
# Keep the dashboard (http://127.0.0.1:5173) visible next to this terminal.
set -u
cd "$(dirname "$0")/.."
# shellcheck disable=SC1090
[ -f "$HOME/.aws_workshop_env" ] && source "$HOME/.aws_workshop_env"

AUTO=0; [ "${1:-}" = "--auto" ] && AUTO=1   # --auto: no Enter needed; pauses and the Stop press are timed
B=$'\033[1m'; DIM=$'\033[2m'; CY=$'\033[36m'; YE=$'\033[33m'; RE=$'\033[31m'; GR=$'\033[32m'; N=$'\033[0m'

agent() { uv run python -m agent "$@"; }

title() { printf '\n%s%s%s\n' "$B$1" "$2" "$N"; }

step() {  # step "<caption>" <agent args...>: shows the caption and the command, waits for Enter, runs it
  local caption="$1"; shift
  printf '\n%s%s%s\n' "$CY" "$caption" "$N"
  printf '%s$ agent %s%s  ' "$DIM" "$*" "$N"
  if [ "$AUTO" = 1 ]; then sleep 2; echo; else read -r _; fi
  agent "$@"
  if [ "$AUTO" = 1 ]; then sleep "${WAIT:-3}"; fi
}

stop_step() {  # the Line 2 shop: in --auto mode, press Stop (the dashboard button's request) during the pause
  local caption="$1"; shift
  if [ "$AUTO" = 0 ]; then step "$caption" "$@"; return; fi
  printf '\n%s%s%s\n' "$CY" "$caption" "$N"
  printf '%s$ agent %s%s  ' "$DIM" "$*" "$N"
  sleep 2; echo
  agent "$@" &
  local shop=$!
  sleep 12
  agent session stop >/dev/null 2>&1
  printf '   %s[Stop pressed by the parent: the same request as the dashboard Stop button]%s\n' "$B$RE" "$N"
  wait "$shop"
  sleep 4
}

# Sign the agent in fresh (logins last 1 hour). The password comes from .env (AWS mode) or is the
# local default (local mode); it is never printed. Falls back to an interactive login.
if [ -z "${AGENT_PASSWORD:-}" ]; then
  if [ "${AGENTPASS_MODE:-}" = "local" ]; then
    AGENT_PASSWORD=parent-pass
  else
    AGENT_PASSWORD=$(grep -E '^AGENTPASS_DEMO_PASSWORD=' .env 2>/dev/null | head -1 | cut -d= -f2-)
  fi
fi
export AGENT_USER="${AGENT_USER:-parent}" AGENT_PASSWORD
if ! agent login >/dev/null 2>&1; then
  printf '%sAutomatic sign-in failed; sign in by hand:%s\n' "$RE" "$N"
  agent login || exit 1
fi

if [ "$AUTO" = 1 ]; then
  printf '%sAide-commerce live demo%s\n' "$B" "$N"
else
  printf '%sAide-commerce live demo%s  (press Enter to run each step; click this window first)\n' "$B" "$N"
fi
if [ "$AUTO" = 0 ]; then
  printf '%sPress Enter to reset the demo (clean budget, no sessions)...%s' "$DIM" "$N"
  read -r _
fi
agent demo reset >/dev/null || { printf '%sNot logged in? Run: uv run python -m agent login%s\n' "$RE" "$N"; exit 1; }

title "$GR" "LINE 1 · NORMAL FLOW: authenticate, browse, order"
WAIT=5 step "The agent gets its own passport: acting for the parent, 5-minute lifetime" session start
WAIT=4 step "It sees only 6 shopper tools: no gift cards, no address changes" tools
WAIT=4 step "A real Claude agent (Amazon Bedrock) shops within the \$50 mandate" shop "Buy one pack of notebooks and one pack of pens for school, then check out to my home address. Stay under budget." --driver bedrock

title "$RE" "LINE 2 · REVOKE FLOW: explicit revoke, the agent fails, your data stays unused"
WAIT=3 step "A new task: the agent starts shopping again (nothing to click yet)" session start
stop_step "The agent fills the cart, then pauses before paying: then the parent presses Stop" shop --scenario stop
WAIT=4 step "The copied token is useless now, even with minutes left on it" replay --expect 401

title "$YE" "ENDING · A HIJACKED AGENT (simulated: real Claude refused this injection in our tests)"
WAIT=3 step "A new task for a compromised agent" session start
WAIT=6 step "It follows a hidden instruction in a product listing" shop --scenario hijacked --step-delay 1.5

printf '\n%sDemo complete.%s Gift cards blocked, attacker address blocked, agent revoked automatically: $0 lost.\n' "$B" "$N"
