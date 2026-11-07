# tests/

**Owner:** Larry (system architect).

```bash
pytest                      # 35 tests, about 3 seconds, no AWS needed
tests/demo_reset.sh         # demo reset + warm-up (login, reset, session, tools/list)
tests/replay_token.sh       # curl replay of the copied passport after Stop -> expects HTTP 401
```

- `test_policy.py`: one test per Cedar / argument rule, plus the fail-closed case (Cedar evaluation error -> deny).
- `test_tokens.py`: passport claims, expiry, audience, tampering, unknown key.
- `test_e2e.py`: starts a real store MCP server and a real agentpass server on free ports (local mode) and runs
  the four A10 steps: passport issued, only shopper tools visible, happy checkout with budget accounting,
  over-budget denial, hijacked agent (2 denials -> auto-revoke -> 401 on the next call, on a replayed token
  and on a new exchange), Stop button, hidden tool denial, bad arguments, store errors, and the scripted agent
  through the real broker and MCP client.

M1/M2 sign-off = `pytest` green three times in a row after `tests/demo_reset.sh`.
