"""Verify Cognito user access tokens (the human's login)."""

from __future__ import annotations

import jwt
from fastapi import Header, HTTPException

from .config import COGNITO_CLIENT_ID, COGNITO_USER_POOL_ID, REGION

_ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
_jwks = jwt.PyJWKClient(f"{_ISSUER}/.well-known/jwks.json", cache_keys=True)


def verify_access_token(token: str) -> dict:
    key = _jwks.get_signing_key_from_jwt(token).key
    # Cognito access tokens carry client_id instead of aud.
    claims = jwt.decode(
        token, key, algorithms=["RS256"], issuer=_ISSUER, leeway=5,
        options={"require": ["exp", "iat", "sub"], "verify_aud": False},
    )
    if claims.get("token_use") != "access" or claims.get("client_id") != COGNITO_CLIENT_ID:
        raise jwt.InvalidTokenError("not an AgentPass user access token")
    return claims


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency for Control API routes: requires a valid Cognito login."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "login required")
    try:
        return verify_access_token(authorization.removeprefix("Bearer "))
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid login token: {e}") from e
