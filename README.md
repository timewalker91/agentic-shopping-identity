# AgentPass: Agentic Shopping Identity

> AWS x A10 Hack 2026, Santa Clara University · **A10 Track, Prompt 1: Agentic Identity**

AI shopping agents can now check out on their own. This project gives every shopping agent its own scoped, short-lived identity, enforces what it may do on **every MCP tool call**, and lets a parent or an automatic risk signal **revoke the agent mid-task**. Once revoked, the agent's next request is blocked, even if its token hasn't expired.

## Demo requirements (A10 Prompt 1)

- [ ] Authenticate an agent (Agent Passport: on-behalf-of token)
- [ ] Allow selected MCP tools (`search`, `add_to_cart`, `checkout` ✅ · `buy_gift_card`, `change_shipping_address` ❌)
- [ ] Revoke access mid-task (poisoned product listing → auto-revoke, or parent kill switch)
- [ ] Block subsequent requests (retries with the unexpired token → 401)

## Repo layout

| Folder | What goes here |
|---|---|
| `agentpass/` | FastAPI app on :8000: token service (KMS-signed delegated tokens), MCP gateway, Cedar policies, Control API |
| `agent/` | Shopping agent (Python + Amazon Bedrock) + credential broker |
| `store/` | Mock store MCP server + payment mock + seed catalog (with one poisoned listing) |
| `infra/` | `setup.py`: DynamoDB tables, Cognito user pool, KMS keys (boto3) |
| `dashboard/` | React live security dashboard: Agent Passport, decision feed, budget gauge, Stop button |
| `tests/` | Rule tests, token-replay script, demo reset |
| `docs/` | Implementation plan, scenario map, architecture |

## Run it

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate     # or python3.12 -m venv .venv
uv pip install -e ".[dev]"                                    # or pip install -e ".[dev]"
pytest                                                        # 35 tests, no AWS needed
```

Three terminals, local mode (no AWS; in-memory tables, local signing key, user `parent` / `parent-pass`):

```bash
python -m store.server          # store MCP server on :8100 (mock catalog; STORE_BACKEND=shopify for the dev store)
python -m agentpass             # token service + gateway + Control API on :8000
agent login --user parent --password parent-pass
agent session start             # Cognito/local login -> session -> KMS/local-signed passport (5 min)
agent tools                     # the agent sees only the 6 shopper tools
agent shop --scenario happy     # S1: search, add, checkout within budget
agent session start && agent shop --scenario hijacked   # S3: poisoned listing -> 2 denials -> auto-revoke -> 401
agent replay --expect 401       # the copied, unexpired token is blocked
tests/replay_token.sh           # same with curl
```

`agent shop --driver bedrock "buy notebooks, pens and glue under $50"` runs the Amazon Bedrock agent instead of the
scripted scenarios. AWS mode: `source ~/.aws_workshop_env`, `python -m infra.setup --demo-password '...'`, then start
`agentpass` again; it picks up `AGENTPASS_MODE=aws` from `.env` (DynamoDB, KMS signing, Cognito login).
Each folder's README has the details; `agentpass/README.md` documents the HTTP API the dashboard uses.

## Team

| Name | GitHub | Role |
|---|---|---|
| Jack Ning | @timewalker91 | Backend: token service, gateway, Cedar, agent, store, infra |
| Isabella | @Isabellazzz | Dashboard (React) + Touch ID step-up page |
| Larry | | System architect: contracts, integration (agent / store / AWS), quality gates, tests, demo environment |
| Eric | | Slides, business plan, Devpost, Q&A |

AI tools: read [`CLAUDE.md`](CLAUDE.md) and [`docs/implementation-plan.md`](docs/implementation-plan.md) first.

## Team rules

- **Never commit AWS credentials** (`.env`, `~/.aws_workshop_env`, access keys). This repo is public.
- Commit small and often, **each person from their own GitHub account**. The commit history is our proof of work for the judges.
- No pre-written code, templates, or v0/Lovable-style generators (hackathon rules). Organizer sample code and normal packages are fine.
- AWS region: `us-east-1` only.

## Deadline

Devpost submission: **Sun Nov 8, 2026, 9:00 AM PST**. Judging in Heafey Atrium starts ~9:30 AM.
