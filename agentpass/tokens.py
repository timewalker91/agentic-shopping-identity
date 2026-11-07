"""Delegated tokens: the Agent Passport.

Claims follow the plan, section 5 (flat so Cedar can read them):
  iss, sub (= user), aud, act {sub, sub_profile}, agent_id, sid, mandate_id,
  tool_profile, merchant, ship_to_id, scope, jti, iat, exp
"""
from __future__ import annotations

import base64
import json
import secrets

import jwt
from jwt.algorithms import ECAlgorithm

from .clock import now_epoch
from .signer import Signer

REQUIRED_CLAIMS = ("sid", "agent_id", "mandate_id", "tool_profile", "merchant", "ship_to_id", "jti")
SCOPE = "catalog:read cart:write checkout:execute"


class TokenError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_jwt(signer: Signer, claims: dict) -> str:
    header = {"alg": signer.alg, "typ": "JWT", "kid": signer.kid}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":"), sort_keys=True).encode()) for part in (header, claims)
    )
    return f"{signing_input}.{_b64url(signer.sign(signing_input.encode('ascii')))}"


def delegated_claims(
    *,
    issuer: str,
    audience: str,
    user_sub: str,
    agent_id: str,
    sid: str,
    mandate: dict,
    ttl_seconds: int,
) -> dict:
    now = now_epoch()
    return {
        "iss": issuer,
        "sub": user_sub,
        "aud": audience,
        "act": {"sub": agent_id, "sub_profile": "ai_agent"},
        "agent_id": agent_id,
        "sid": sid,
        "mandate_id": mandate["mandate_id"],
        "tool_profile": mandate["tool_profile"],
        "merchant": mandate["merchant"],
        "ship_to_id": mandate["ship_to_id"],
        "scope": SCOPE,
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl_seconds,
    }


class TokenVerifier:
    """Verifies delegated tokens against the JWKS of the token service."""

    def __init__(self, jwks: list[dict], issuer: str, audience: str) -> None:
        self._keys = {k["kid"]: ECAlgorithm.from_jwk(json.dumps(k)) for k in jwks}
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> dict:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError as exc:
            raise TokenError("invalid_token", f"malformed token: {exc}") from exc
        key = self._keys.get(kid)
        if key is None:
            raise TokenError("invalid_token", "unknown signing key")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["ES256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenError("token_expired", "delegated token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError("invalid_token", f"token rejected: {exc}") from exc
        missing = [c for c in REQUIRED_CLAIMS if not claims.get(c)]
        if missing:
            raise TokenError("invalid_token", f"token is missing claims: {', '.join(missing)}")
        return claims


def unverified_claims(token: str) -> dict:
    """Claims without signature verification. Display and best-effort audit only, never authorization."""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return {}
