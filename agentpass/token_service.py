"""AgentPass token service: RFC 8693 token exchange.

The credential broker sends: the user's Cognito access token (subject) + a client
assertion signed by the agent's own KMS key (actor). If every check passes, we
return a 5-minute delegated token signed by the AgentPass KMS key:
sub = user, act = agent, plus the session, mandate and tool profile.
"""

from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import cognito_auth
from .audit import record
from .aws import table
from .config import (
    AGENTS, ASSERTION_MAX_TTL, AUDIENCE, BASE_URL, DELEGATED_TOKEN_TTL, ISSUER,
    T_MANDATES, T_REVOCATIONS, T_SESSIONS, TOKEN_ENDPOINT, TOKEN_SIGNER_KEY_ARN,
)
from .kms_jwt import jwk, sign_jwt, verify_jwt
from .revocation import RevocationLookupError, revoked

router = APIRouter()

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"


class GrantError(Exception):
    """Any failed check. The token service answers 400 invalid_grant with this reason."""


@router.get("/.well-known/openid-configuration")
def discovery() -> dict:
    return {"issuer": ISSUER, "jwks_uri": f"{BASE_URL}/.well-known/jwks.json", "token_endpoint": TOKEN_ENDPOINT}


@router.get("/.well-known/jwks.json")
def jwks() -> dict:
    return {"keys": [jwk(TOKEN_SIGNER_KEY_ARN)]}


@router.post("/oauth2/token")
async def token_exchange(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    try:
        return issue_delegated_token(form)
    except GrantError as e:
        sid = form.get("session_id")
        if sid:
            record(sid, agent_id="?", user="?", tool="token_exchange", decision="deny", reason=str(e), layer="token")
        return JSONResponse(status_code=400, content={"error": "invalid_grant", "error_description": str(e)})


def issue_delegated_token(form: dict) -> dict:
    if form.get("grant_type") != GRANT_TYPE:
        raise GrantError("unsupported grant_type")
    if form.get("subject_token_type") != ACCESS_TOKEN_TYPE or form.get("actor_token_type") != JWT_TOKEN_TYPE:
        raise GrantError("unsupported token type")
    if form.get("audience") != AUDIENCE:
        raise GrantError("audience must be agentpass-gateway")

    # 1. The human: a valid Cognito access token for our app.
    try:
        user = cognito_auth.verify_access_token(form.get("subject_token", ""))
    except jwt.PyJWTError as e:
        raise GrantError(f"subject_token invalid: {e}") from e

    # 2. The agent: a fresh, single-use assertion signed by its registered KMS key.
    agent_id, agent = verify_actor_assertion(form.get("actor_token", ""))

    # 3. The task: the session must belong to this user and this agent.
    sid = form.get("session_id", "")
    session = table(T_SESSIONS).get_item(Key={"sid": sid}, ConsistentRead=True).get("Item")
    if not session or session["user_id"] != user["sub"] or session["agent_id"] != agent_id:
        raise GrantError("unknown session for this user and agent")
    if session.get("status") != "active":
        raise GrantError(f"session {session.get('status')}")

    # 4. The mandate must be active and unexpired.
    mandate = table(T_MANDATES).get_item(Key={"mandate_id": session["mandate_id"]}, ConsistentRead=True).get("Item")
    now = int(time.time())
    if not mandate or mandate.get("status") != "active" or int(mandate["expires_at"]) <= now:
        raise GrantError("mandate inactive or expired")

    # 5. Nothing involved may be revoked. Fail closed if the list can't be read.
    try:
        hits = revoked([f"user#{user['sub']}", f"agent#{agent_id}", f"mandate#{mandate['mandate_id']}", f"sid#{sid}"])
    except RevocationLookupError as e:
        raise GrantError("revocation check unavailable") from e
    if hits:
        raise GrantError(f"revoked: {hits[0]}")

    # 6. Granted scope = requested scope ∩ the agent's tool profile.
    requested = set(form.get("scope", "").split()) or agent["scopes"]
    granted = requested & agent["scopes"]
    if not granted:
        raise GrantError("no requested scope is allowed for this agent")

    claims = {
        "iss": ISSUER,
        "sub": user["sub"],
        "aud": AUDIENCE,
        "act": {"sub": agent_id, "sub_profile": "ai_agent"},
        # Flat copies so Cedar can read them as principal attributes.
        "agent_id": agent_id,
        "sid": sid,
        "mandate_id": mandate["mandate_id"],
        "tool_profile": agent["tool_profile"],
        "merchant": mandate["merchant"],
        "ship_to_id": mandate["ship_to_id"],
        "scope": " ".join(sorted(granted)),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + DELEGATED_TOKEN_TTL,
    }
    token = sign_jwt(TOKEN_SIGNER_KEY_ARN, claims)
    record(sid, agent_id=agent_id, user=user["sub"], tool="token_exchange", decision="allow",
           reason=f"delegated token issued, expires in {DELEGATED_TOKEN_TTL}s", layer="token")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": DELEGATED_TOKEN_TTL,
        "issued_token_type": ACCESS_TOKEN_TYPE,
        "scope": claims["scope"],
    }


def verify_actor_assertion(assertion: str) -> tuple[str, dict]:
    try:
        agent_id = jwt.decode(assertion, options={"verify_signature": False}).get("iss", "")
    except jwt.PyJWTError as e:
        raise GrantError(f"actor_token unreadable: {e}") from e
    agent = AGENTS.get(agent_id)
    if not agent:
        raise GrantError("unknown agent")
    try:
        claims = verify_jwt(assertion, agent["key_arn"], audience=TOKEN_ENDPOINT, issuer=agent_id,
                            require=["exp", "iat", "jti", "sub"])
    except jwt.PyJWTError as e:
        raise GrantError(f"actor_token invalid: {e}") from e
    if claims["sub"] != agent_id or claims["exp"] - claims["iat"] > ASSERTION_MAX_TTL:
        raise GrantError("actor_token must have sub = iss and live at most 60s")

    # Single use: remember the assertion's jti for 10 minutes; a second use is a replay.
    try:
        table(T_REVOCATIONS).put_item(
            Item={"key": f"assertion#{claims['jti']}", "level": "assertion", "ttl": int(time.time()) + 600},
            ConditionExpression="attribute_not_exists(#k)",
            ExpressionAttributeNames={"#k": "key"},
        )
    except table(T_REVOCATIONS).meta.client.exceptions.ConditionalCheckFailedException as e:
        raise GrantError("actor_token replayed") from e
    return agent_id, agent
