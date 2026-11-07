#!/usr/bin/env bash
# Demo reset + warm-up: clean tables, restore the store, log in, start a session, list tools once.
set -euo pipefail
export AGENT_USER="${AGENT_USER:-parent}"
export AGENT_PASSWORD="${AGENT_PASSWORD:-parent-pass}"
python -m agent login
python -m agent demo reset
python -m agent session start
python -m agent tools
echo "warm-up done; passport issued. Run: python -m agent shop --scenario happy"
