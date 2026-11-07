"""Token service: RFC 8693 token exchange and the JWKS endpoint.

POST /oauth2/token (form or JSON)
    grant_type         = urn:ietf:params:oauth:grant-type:token-exchange
    subject_token      = the user's id token (Cognito, or local in local mode)
    subject_token_type = urn:ietf:params:oauth:token-type:id_token
    actor_token        = the agent id (agent_shopper_v1)
    actor_token_type   = urn:agentpass:params:oauth:token-type:agent-id
    sid                = the session from POST /sessions

The exchange fails for a revoked session, user, agent or mandate, so "block subsequent
requests" also covers getting a fresh token.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .audit import new_event
from .clock import now_iso
from .revocation import keys_for_exchange
from .storage import RevocationError
from .tokens import delegated_claims, encode_jwt
from .users import UserAuthError

router = APIRouter()
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ISSUED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"


async def _params(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = await request.json()
        return body if isinstance(body, dict) else {}
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


def _fail(status: int, error: str, reason: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description, "reason": reason, "message": description},
        status_code=status,
    )


@router.post("/oauth2/token", response_model=None)
async def token_exchange(request: Request) -> JSONResponse:
    state = request.app.state
    settings = state.settings
    params = await _params(request)

    if params.get("grant_type") != GRANT_TYPE:
        return _fail(400, "unsupported_grant_type", "unsupported_grant_type", f"grant_type must be {GRANT_TYPE}")
    subject_token = params.get("subject_token")
    sid = params.get("sid")
    actor = params.get("actor_token") or settings.agent_id
    if not subject_token or not sid:
        return _fail(400, "invalid_request", "invalid_request", "subject_token and sid are required")

    try:
        identity = state.user_auth.verify(subject_token)
    except UserAuthError as exc:
        return _fail(401, "invalid_grant", "invalid_subject_token", exc.message)

    session = state.repo.get_session(sid)
    if session is None or session.get("user") != identity.sub:
        return _fail(401, "invalid_grant", "unknown_session", f"no session {sid} for this user")
    if actor != session.get("agent_id"):
        return _fail(401, "invalid_grant", "agent_mismatch", "actor_token does not match the session's agent")

    def audit(decision: str, layer: str, reason: str) -> None:
        state.repo.append_audit(
            new_event(
                sid=sid,
                agent_id=actor,
                user=identity.username,
                tool="token_exchange",
                decision=decision,
                reason=reason,
                layer=layer,
                args={"sid": sid, "actor": actor},
                latency_ms=0,
            )
        )

    if session.get("status") != "active":
        audit("revoked", "revocation", f"exchange refused: session {session.get('status')}")
        return _fail(401, "invalid_grant", "session_revoked", "the session is revoked; no new passport")
    try:
        hit = state.repo.find_revoked(
            keys_for_exchange(sid=sid, user=identity.sub, agent_id=actor, mandate_id=session["mandate_id"])
        )
    except RevocationError as exc:
        return _fail(503, "temporarily_unavailable", "revocation_check_failed", str(exc))
    if hit:
        audit("revoked", "revocation", f"exchange refused: {hit['key']} revoked ({hit.get('reason')})")
        return _fail(401, "invalid_grant", "session_revoked", f"revoked: {hit.get('reason')}")

    mandate = state.repo.get_mandate(session["mandate_id"])
    if mandate is None or mandate.get("status") != "active":
        audit("deny", "policy", "exchange refused: mandate inactive")
        return _fail(403, "invalid_grant", "mandate_inactive", "the mandate is not active")

    claims = delegated_claims(
        issuer=settings.issuer,
        audience=settings.audience,
        user_sub=identity.sub,
        agent_id=actor,
        sid=sid,
        mandate=mandate,
        ttl_seconds=settings.token_ttl_seconds,
    )
    token = encode_jwt(state.signer, claims)
    state.repo.update_session(sid, passport=claims, last_jti=claims["jti"], token_issued_at=now_iso())
    audit("allow", "token", f"passport issued (kid {state.signer.kid}, {settings.token_ttl_seconds}s)")
    return JSONResponse(
        {
            "access_token": token,
            "issued_token_type": ISSUED_TOKEN_TYPE,
            "token_type": "Bearer",
            "expires_in": settings.token_ttl_seconds,
            "scope": claims["scope"],
            "sid": sid,
            "claims": claims,
        }
    )


@router.get("/.well-known/jwks.json")
def jwks(request: Request) -> dict:
    return {"keys": [request.app.state.signer.public_jwk()]}
