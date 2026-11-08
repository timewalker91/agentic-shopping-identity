# Aide-commerce: instructions for AI coding tools

Read this before writing any code in this repo. It applies to Claude Code, Kiro, or any other assistant a teammate uses.

## What we're building
Aide-commerce gives an AI shopping agent its own short-lived, KMS-signed identity. A gateway checks every MCP tool call against that identity, Cedar policies, a revocation list and a budget. The user (or an automatic rule) can revoke the agent mid-task, and every later request is blocked. This is the AWS x A10 Hack 2026, A10 Prompt 1.

**Naming:** the product is **Aide-commerce**. The lowercase codename `agentpass` stays in code identifiers on purpose: the `agentpass/` package, token `iss`/`aud`, AWS resource names (`alias/agentpass-*`, `agentpass-users`) and the `ap_` tables. Don't rename these; the live AWS setup and teammates' code depend on them.

**Source of truth:** `docs/implementation-plan.md`, especially **section 5, Contracts**. Follow it exactly. **Larry (system architect) owns the contracts:** if one seems wrong, stop and ask Larry; don't silently change it.

@docs/implementation-plan.md

## Folder ownership
| Folder | Owner | Contents |
|---|---|---|
| `agentpass/` | Jack | FastAPI app on :8000: token service, gateway (`/mcp`), Control API, Cedar policies |
| `agent/` | Larry | Shopping agent (Amazon Bedrock) + credential broker |
| `store/` | Larry | Store MCP server on :8100 (mock, or adapter to a Shopify dev store) + payment mock + `seed.json`; same tool contract either way |
| `infra/` | Jack | `setup.py`: creates the DynamoDB tables, Cognito pool and KMS keys with boto3 |
| `dashboard/` | Isabella | React + Vite on :5173; talks only to the agentpass API, never to AWS directly |
| `tests/` | Larry | End-to-end + rule tests, token-replay script, demo reset |
| `docs/` | Everyone (Larry owns the architecture docs) | Plans, architecture, pitch material |

Only edit another person's folder after telling them.

## Rules
- **Never commit or print AWS credentials.** Code reads credentials from the environment (`~/.aws_workshop_env`). Never hardcode keys or create IAM access keys. Region is always `us-east-1`.
- **The frontend never calls AWS.** The dashboard calls the agentpass API; AWS calls happen only in Python backend code.
- **The model never sees tokens.** Only the credential broker in `agent/` holds and attaches tokens.
- **Fail closed.** If the gateway can't read the revocation list or evaluate policy, deny the call.
- **Never log** Authorization headers, full tokens or payment data.
- **Python:** 3.13 via `uv` (`uv sync`, then `uv run ...`), type hints, small modules, FastAPI + boto3 + cedarpy. **MCP SDK is pinned to 1.x:** use `from mcp.server.fastmcp import FastMCP` for servers and `mcp.client.streamable_http.streamablehttp_client` + `ClientSession` for clients. **Dashboard:** TypeScript + React + Vite.
- **Bedrock models:** the workshop allowlist blocks Claude Sonnet 5.5, Haiku 5.5, Opus 5.5 and Fable 5.1. Use `us.anthropic.claude-sonnet-5`, `us.anthropic.claude-haiku-4-5-20251001-v1:0` or `us.meta.llama4-scout-17b-instruct-v1:0`.
- **Reference code:** `agentpass/smoke_gateway.py` is a working agent-side example (token exchange + MCP calls through the gateway). `agentpass/dev_stub_store.py` is a dev-only stand-in store that follows the store contract.
- Keep changes small and commit often from your own GitHub account. Write clear commit messages.
- No pre-written code, copied projects or one-shot app generators (hackathon rules). Every author must be able to explain their code to the judges.
- Scope: build P0 first (`docs/implementation-plan.md`, section 3). Don't add features beyond the plan without telling the team.
