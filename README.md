# Agentic Shopping Identity

> AWS x A10 Hack 2026, Santa Clara University · **A10 Track, Prompt 1: Agentic Identity**

AI shopping agents can now check out on their own. This project gives every shopping agent its own scoped, short-lived identity, enforces what it may do on **every MCP tool call**, and lets a parent or an automatic risk signal **revoke the agent mid-task**. Once revoked, the agent's next request is blocked, even if its token hasn't expired.

## Demo requirements (A10 Prompt 1)

- [ ] Authenticate an agent (Agent Passport: on-behalf-of token)
- [ ] Allow selected MCP tools (`search`, `add_to_cart`, `checkout` ✅ · `buy_gift_card`, `change_shipping_address` ❌)
- [ ] Revoke access mid-task (poisoned product listing → auto-revoke, or parent kill switch)
- [ ] Block subsequent requests (retries with the unexpired token → 401)

## Repo layout (planned)

| Folder | What goes here |
|---|---|
| `agent/` | Shopping agent (Python + Amazon Bedrock) |
| `store/` | Mock store + MCP server exposing shopping tools |
| `gateway/` | API Gateway + Lambda authorizer (identity, revocation list, tool allowlist, budget) |
| `dashboard/` | React live activity feed, budget meter, Revoke button |
| `docs/` | PRD, scenario map, architecture diagram |

## Team

| Name | GitHub | Role |
|---|---|---|
| Jack Ning | @timewalker91 | Product, pitch, Devpost |
| | | |
| | | |
| | | |

## Team rules

- **Never commit AWS credentials** (`.env`, `~/.aws_workshop_env`, access keys). This repo is public.
- Commit small and often, **each person from their own GitHub account**. The commit history is our proof of work for the judges.
- No pre-written code, templates, or v0/Lovable-style generators (hackathon rules). Organizer sample code and normal packages are fine.
- AWS region: `us-east-1` only.

## Deadline

Devpost submission: **Sun Nov 8, 2026, 9:00 AM PST**. Judging in Heafey Atrium starts ~9:30 AM.
