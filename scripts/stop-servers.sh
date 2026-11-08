#!/usr/bin/env bash
# Stop the store (:8100), the API (:8000) and the dashboard (:5173).
pids=$(lsof -ti tcp:8000,8100,5173 -sTCP:LISTEN 2>/dev/null)
if [ -z "$pids" ]; then echo "Nothing running on :8000, :8100 or :5173"; exit 0; fi
kill $pids && echo "Stopped: $(echo $pids | wc -w | tr -d ' ') process(es)"
