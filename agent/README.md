# agent/

**Owner:** Jack. Shopping agent + credential broker, as the `agent` command-line MCP client.

The broker (`broker.py`) is the only component that holds tokens: it logs the parent in, opens a
session, runs the RFC 8693 token exchange and attaches the delegated token to every MCP request
(`mcp_client.py`). The model never sees a token. State lives in `~/.agentpass/agent-state.json`
(mode 0600; override with `AGENT_STATE_FILE`).

```bash
agent login --user parent --password parent-pass     # or python -m agent ...
agent session start                                   # session + first passport
agent passport                                        # claims + expiry countdown + budget
agent tools                                           # tools/list through the gateway (6 shopper tools)
agent call search_products --args '{"query": "pens"}'
agent shop --scenario happy                           # S1: search, add, checkout within budget
agent shop --scenario over_budget                     # S2: $89 denied by the budget, cheaper cart paid
agent session start && agent shop --scenario hijacked # S3: poisoned listing -> 2 denials -> auto-revoke -> 401
agent replay --expect 401                             # the copied token is replayed: 401
agent session start && agent shop --scenario stop   # S6: press Stop while it waits before checkout
agent session stop                                    # Stop from the command line
agent audit                                           # the session's audit events
agent demo reset
```

Drivers (`--driver` or `AGENT_DRIVER`):

- `scripted` (default): deterministic scenarios in `scripted.py`, used by the tests and the demo. The hijacked run reads the poisoned listing and follows its hidden instruction like a compromised agent would.
- `bedrock`: `shopper.py` runs an Amazon Bedrock Converse loop with the tools the gateway shows. `agent shop --driver bedrock "buy notebooks, pens and glue under $50"`. Model id from `BEDROCK_MODEL_ID` (`agent models` lists what the account can use). Needs AWS credentials in the environment.

Error handling follows the contract: `session_revoked` stops the run with no retry, `policy_denied`
is explained and not retried, `over_budget` leads to cheaper items, `token_expired` triggers one
token refresh through the broker (which fails if the session was revoked).
