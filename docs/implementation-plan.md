# Implementation Plan: AgentPass (Hackathon Build)

**Status:** proposal for team review, Sat Nov 7, 14:30. It builds on the Architecture Design tab.
**Deadline:** Devpost by **Sun Nov 8, 8:30 AM** (hard deadline 9:00). Judging in the Heafey Atrium from 9:30.

## 1. Decision: hybrid build

The Architecture Design is the right production design, but it is about 75–115 person-hours of work. We have about 13 clock hours of building left: Jack drives the backend with Claude Code, and Isabella builds the dashboard. We keep the same design and the same Cedar policies, and swap the heavy managed pieces for thin code we can explain line by line. **AWS stays where it is cheap:** Bedrock, Cognito, DynamoDB and KMS.

| Architecture Design | Hackathon build | Why |
|---|---|---|
| AgentCore Gateway | FastAPI gateway in `agentpass/` (the Design's own fallback) | Full control in the live demo; every line explainable |
| AgentCore Policy (Cedar) | Same Cedar policies, evaluated with `cedarpy` | Same policy text, so it drops into AgentCore Policy unchanged |
| REQUEST / RESPONSE interceptors | Middleware functions in the gateway | Same checks, no Lambda cold starts |
| AgentCore Runtime | Agent runs locally on the demo laptop | Deploying to Runtime moves to P1 |
| Token Service (API GW + Lambda + KMS) | FastAPI route, **still signs with KMS** | KMS-signed delegated token kept |
| Cognito | **Kept:** user login | "Enterprise authentication" in Prompt 1 |
| DynamoDB | **Kept:** 5 tables instead of 9 | |
| EventBridge risk engine | Violation counter inside the gateway | 2 denials → auto-revoke; EventBridge moves to P1 |
| AppSync Events | Dashboard polls `/audit` every second | P1 |
| Amplify Hosting + CDK | localhost + one setup script (`infra/setup.py`) | No deploy risk |

**Architecture slide line:** "Built as a portable gateway; the same Cedar policies run on Amazon Bedrock AgentCore Gateway + Policy in production."

## 2. Workload estimate

**Split:** Jack drives all backend code in one Claude Code (Opus 5.5) session, so the security core stays consistent. The other three own self-contained pieces that touch the core only through the contracts in section 5.

| Component | Owner | Hours (AI-assisted) | Lines |
|---|---|---|---|
| `infra/setup.py`: DynamoDB tables, Cognito pool + test user, KMS keys | **Jack** | 1.5–2 | 100–200 |
| Token service: RFC 8693 exchange, KMS signing, JWKS, revocation check | **Jack** | 3–4 | 250–350 |
| Gateway: token check, revocation, Cedar, budget, violation counter, audit | **Jack** | 5–7 | 400–600 |
| Cedar policies + entity mapping | **Jack** | 1 | 80–150 |
| Control API: sessions, stop, audit, mandate, demo reset | **Jack** | 1.5–2 | 150–250 |
| Store MCP server + payment mock (code) | **Jack** | 2–3 | 300–450 |
| Shopping agent (Bedrock) + credential broker | **Jack** | 3–4 | 200–350 |
| Integration + end-to-end debugging | **Jack** (Larry tests) | 3–4 | – |
| Dashboard: login, mandate, Passport card, live feed, budget gauge, Stop | **Isabella** | 5–7 | 700–1,000 |
| *P1: Touch ID step-up page (WebAuthn bound to the cart hash)* | **Isabella** (UI) + Jack (API) | 3–4 | 250–350 |
| Seed catalog JSON + the poisoned listing text, rule tests, token-replay script, demo reset runs, injection tuning, backup video | **Larry** | 4–5 | 200–300 |
| Slides, business plan, Devpost, Q&A sheet; learn the architecture with Jack | **Eric** | 5–6 | – |
| **Total** | | | **~2,600–4,000** |

**Jack is the critical path:** about 20–27 AI-assisted hours of backend work in about 13 clock hours. That only works with parallel Claude Code sessions and no scope creep.
- **Overflow rule:** if M1 isn't green by 20:00, Larry takes over the store code. If the dashboard is ahead of schedule, Isabella takes over the Control API endpoints.
- **P1 starts only if Milestone 2 is on time.**

## 3. Scope

**P0: the demo must show all of this**
- Authenticate the agent: Cognito login → KMS-signed delegated token (sub = user, act = agent, 5-minute lifetime).
- Allow selected MCP tools: the agent sees only shopper tools; Cedar denies gift-card SKUs, other shipping addresses and quantities over 10.
- Revoke mid-task: Stop button, plus auto-revoke after 2 denials in 5 minutes (the hijacked-agent run).
- Block subsequent requests: the agent's next call gets 401, a curl replay of the copied token gets 401, and a new token exchange fails.
- Budget: a within-budget checkout passes; an over-budget checkout is denied (server-side cart total).
- Dashboard: Passport card (claims + expiry countdown), live decision feed, budget gauge, Stop button.

**P1: only after Milestone 2**
Touch ID step-up → content-filter toggle → Agent on AgentCore Runtime + Kill → push-based live feed.

**Roadmap (slides only)**
Recurring mandates, parental approval queue, virtual cards / Stripe, CAEP events to merchants, lockdown + global sign-out, Bedrock Guardrails classifier, DPoP.

## 4. Timeline

| Time | Phase | Done when |
|---|---|---|
| 14:30–15:30 | Everyone joins AWS (@scu.edu). Bedrock check. **Freeze the contracts (section 5).** Repo skeleton pushed. | Each builder can call Bedrock and DynamoDB from their laptop |
| 15:30–20:00 | Build components in parallel against the contracts | **M1 (20:00):** one allowed tool call goes login → token → gateway → Cedar → store and shows on the dashboard |
| 20:00–00:30 | Stop → 401, Cedar enforce, budget, injection → auto-revoke, live dashboard | **M2 (00:30):** the full P0 demo passes 3 times in a row after a reset |
| 00:30–03:30 | P1 (Touch ID first), UI polish, sleep in shifts | **Feature freeze 03:30** |
| 03:30–07:30 | Backup video, revoke-moment GIF, slides with business model, Devpost, README | Each person can explain their part in 1 minute |
| 07:30–08:30 | Rehearse 3 times with Q&A drill, **submit Devpost** | At the Heafey Atrium by 9:00 |

## 5. Contracts: freeze by 15:30, change only after telling the whole team

**Processes and ports.** `agentpass` FastAPI (token service + gateway + control API) on `:8000` · `store` MCP server on `:8100` (reachable only by the gateway) · `dashboard` Vite on `:5173` · `agent` command-line client. Region `us-east-1`.

**Delegated token claims** (as in the Architecture Design; flat claims so Cedar can read them):

```json
{"iss":"agentpass","sub":"<cognito-sub>","aud":"agentpass-gateway",
 "act":{"sub":"agent_shopper_v1","sub_profile":"ai_agent"},
 "agent_id":"agent_shopper_v1","sid":"sess_…","mandate_id":"mdt_…",
 "tool_profile":"shopper_v1","merchant":"freshmart","ship_to_id":"addr_home",
 "scope":"catalog:read cart:write checkout:execute","jti":"…","iat":0,"exp":0}
```

**MCP tools** (agent → gateway: MCP over HTTP at `POST :8000/mcp`, header `Authorization: Bearer <delegated token>`)

| Tool | Input | Visible to agent | Rule |
|---|---|---|---|
| `search_products` | `query`, `category?` | Yes | Permit for shopper_v1 |
| `get_product` | `sku` | Yes | Permit (one listing contains hidden instructions) |
| `view_cart` | `cart_id` | Yes | Permit |
| `add_to_cart` | `cart_id`, `sku`, `quantity`, `merchant` | Yes | Merchant must match the token; quantity ≤ 10; forbid `GC-*` SKUs |
| `checkout` | `cart_id`, `ship_to_id` | Yes | `ship_to_id` must match the token; budget hold on the server-side total |
| `get_order_status` | `order_id` | Yes | Permit |
| `change_shipping_address`, `buy_gift_card` | – | **No** | Forbid for all agents |

**Errors** (JSON-RPC error code `-32000`, reason in `error.data.reason`):

| Reason | HTTP | Agent behavior |
|---|---|---|
| `session_revoked` | 401 | Stop the task, no retry |
| `policy_denied` | 403 | Don't retry; explain to the user |
| `over_budget` (with `remaining`) | 403 | Suggest cheaper items |
| `mandate_inactive` | 403 | Stop the task |
| `step_up_required` (P1) | 403 | Wait for approval, then retry once |

**Audit event** (one per tool call; the dashboard reads these):

```json
{"ts":"…","sid":"…","agent_id":"…","user":"…","tool":"checkout",
 "decision":"allow|deny|revoked","reason":"…",
 "layer":"token|revocation|policy|budget|risk","args_digest":"sha256:…","latency_ms":7}
```

**Control API** (Cognito login required):
- `POST /sessions {mandate_id}` → `{sid}`
- `POST /sessions/{sid}/stop`
- `GET /sessions/{sid}/audit?after=<ts>`
- `GET /mandates/{id}` → budget fields `max`, `spent`, `held`
- `POST /demo/reset`

**DynamoDB tables** (prefix `ap_`):

| Table | Key |
|---|---|
| `ap_mandates` | `mandate_id` |
| `ap_sessions` | `sid` |
| `ap_revocations` | `key`: one of `sid#…`, `agent#…`, `mandate#…`, `user#…`, `jti#…` |
| `ap_store` | `pk`: `product#SKU` or `cart#ID` |
| `ap_audit` | `sid` + `ts#id` |

**Repo layout:** `agentpass/` · `store/` · `agent/` · `dashboard/` · `infra/` · `tests/` · `docs/`

## 6. Team rules
- Commit small and often, from your own GitHub account. Never commit AWS keys (`.gitignore` already blocks them).
- Before you commit AI-written code, read it and be able to explain it. Judges will ask.
- If something blocks you for more than 45 minutes, say so in the team chat and take the simpler path.
- Everyone's AI tool (Claude Code, Kiro, or other) reads `CLAUDE.md` and this plan before writing code. Consistency comes from the contracts, not from using one model.
- **Open questions for 15:30:** (1) Does the team agree on the hybrid build? (2) Are the owners right? (3) Which Bedrock model? Claude if the account has access, otherwise Llama 4 Scout.
