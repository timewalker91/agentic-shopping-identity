# AgentPass: instructions for AI coding tools

Read this before writing any code in this repo. It applies to Claude Code, Kiro, or any other assistant a teammate uses.

## What we're building
AgentPass gives an AI shopping agent its own short-lived, KMS-signed identity. A gateway checks every MCP tool call against that identity, Cedar policies, a revocation list and a budget. The user (or an automatic rule) can revoke the agent mid-task, and every later request is blocked. This is the AWS x A10 Hack 2026, A10 Prompt 1.

**Source of truth:** `docs/implementation-plan.md`, especially **section 5, Contracts**. Follow it exactly. If a contract seems wrong, stop and ask the team; don't silently change it.

@docs/implementation-plan.md

## Folder ownership
| Folder | Owner | Contents |
|---|---|---|
| `agentpass/` | Jack | FastAPI app on :8000: token service, gateway (`/mcp`), Control API, Cedar policies |
| `agent/` | Jack | Shopping agent (Amazon Bedrock) + credential broker |
| `store/` | Jack (code), Larry (seed data) | Mock store MCP server on :8100 + payment mock + `seed.json` |
| `infra/` | Jack | `setup.py`: creates the DynamoDB tables, Cognito pool and KMS keys with boto3 |
| `dashboard/` | Isabella | React + Vite on :5173; talks only to the agentpass API, never to AWS directly |
| `tests/` | Larry | Rule tests, token-replay script, demo reset |
| `docs/` | Everyone | Plans, architecture, pitch material |

Only edit another person's folder after telling them.

## Rules
- **Never commit or print AWS credentials.** Code reads credentials from the environment (`~/.aws_workshop_env`). Never hardcode keys or create IAM access keys. Region is always `us-east-1`.
- **The frontend never calls AWS.** The dashboard calls the agentpass API; AWS calls happen only in Python backend code.
- **The model never sees tokens.** Only the credential broker in `agent/` holds and attaches tokens.
- **Fail closed.** If the gateway can't read the revocation list or evaluate policy, deny the call.
- **Never log** Authorization headers, full tokens or payment data.
- **Python:** 3.12+, type hints, small modules, FastAPI + boto3 + cedarpy + the `mcp` SDK. **Dashboard:** TypeScript + React + Vite.
- Keep changes small and commit often from your own GitHub account. Write clear commit messages.
- No pre-written code, copied projects or one-shot app generators (hackathon rules). Every author must be able to explain their code to the judges.
- Scope: build P0 first (`docs/implementation-plan.md`, section 3). Don't add features beyond the plan without telling the team.
