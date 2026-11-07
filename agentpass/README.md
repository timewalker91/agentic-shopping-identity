# agentpass/

**Owner:** Jack. FastAPI app on `:8000`: token service, MCP gateway, Control API, Cedar policies.
Contracts: `docs/implementation-plan.md`, section 5.

## Run

```bash
source .venv/bin/activate            # see the root README for setup
python -m agentpass                  # local mode by default (no AWS needed)
```

Modes (`AGENTPASS_MODE`, auto-detected from `.env`):

| | `local` | `aws` |
|---|---|---|
| Tables | in memory | DynamoDB `ap_*` (`infra/setup.py`) |
| Token signing | EC P-256 key generated at start | **KMS** asymmetric key, ES256 |
| User login | `AGENTPASS_LOCAL_USERS` (`parent:parent-pass`) | Cognito `USER_PASSWORD_AUTH`, server side |

Same code paths either way; only `signer.py`, `storage.py` and `users.py` switch implementation.

## Modules

| File | What it does |
|---|---|
| `main.py` | app factory, wires everything into `app.state` |
| `token_service.py` | `POST /oauth2/token` (RFC 8693 exchange), `GET /.well-known/jwks.json` |
| `gateway.py` | `POST /mcp`: token -> revocation -> session -> Cedar -> budget -> store, one audit event per call |
| `policy.py` + `policies/shopper.cedar` | argument validation (pydantic) and Cedar evaluation, fail closed |
| `control.py` | login, sessions, Stop, audit feed, mandates, demo reset |
| `storage.py` | `MemoryRepo` / `DynamoRepo` behind one interface (cents, strongly consistent reads) |
| `signer.py`, `tokens.py` | local / KMS signer, passport claims, verifier |
| `users.py` | local / Cognito user authentication |
| `risk.py` | violation counter: 2 policy denials in 5 min -> session revoked |
| `revocation.py`, `audit.py`, `budget.py`, `clock.py` | small helpers |

## HTTP API (for the dashboard and the agent)

All Control API routes need `Authorization: Bearer <id_token>` from `/auth/login`.
Errors are JSON `{"error": "<reason>", "message": "..."}`.

| Route | Body / query | Returns |
|---|---|---|
| `POST /auth/login` | `{username, password}` | `{id_token, expires_in, user:{sub, username}, mandates:[...]}` |
| `GET /me` | | user, mandates, recent sessions |
| `POST /sessions` | `{mandate_id}` | session `{sid, status, agent_id, mandate_id, passport, budget, ...}` (201) |
| `GET /sessions` | | `{sessions:[...]}` newest first |
| `GET /sessions/{sid}` | | session + `passport` (claims of the latest token, with `exp`) + `budget` + `revocation` |
| `POST /sessions/{sid}/stop` | | the session with `status: "revoked"` (the Stop button) |
| `GET /sessions/{sid}/audit?after=<ts>` | | `{sid, events:[...], cursor}`; poll once a second with the last `cursor` |
| `GET /mandates/{id}` | | `{mandate_id, merchant, ship_to_id, status, max, spent, held, remaining, ...}` |
| `POST /mandates` | `{max, merchant?, ship_to_id?}` | a new mandate (201) |
| `POST /demo/reset` | | clears all tables, resets the store, recreates the demo mandate |
| `POST /oauth2/token` | form: `grant_type`, `subject_token` (id token), `actor_token` (agent id), `sid` | `{access_token, expires_in: 300, claims, ...}`; 401 `session_revoked` after a revoke |
| `POST /mcp` | JSON-RPC `initialize`, `tools/list`, `tools/call` with `Authorization: Bearer <delegated token>` | MCP result, or JSON-RPC error `-32000` with `error.data.reason` and the HTTP status from the contract |
| `GET /health` | | mode, signing key id, store url |

Audit event fields: `ts, id, sid, agent_id, user, tool, decision (allow|deny|revoked), reason, layer (token|revocation|policy|budget|risk|gateway), args_digest, latency_ms`.
`token_exchange` and `stop` show up as tools too, so the feed can show "passport issued" and "Stop pressed".

Extra `reason` values beyond the contract table, all fail-closed: `invalid_token`, `token_expired` (401),
`invalid_arguments` (400), `revocation_check_failed`, `store_unavailable`, `gateway_error` (503).
Layer `gateway` is used for the last two.
