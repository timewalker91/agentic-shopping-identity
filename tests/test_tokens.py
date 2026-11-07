"""Delegated token minting and verification."""
import time

import pytest

from agentpass.signer import LocalSigner
from agentpass.tokens import TokenError, TokenVerifier, delegated_claims, encode_jwt, unverified_claims

MANDATE = {"mandate_id": "mdt_demo", "tool_profile": "shopper_v1", "merchant": "freshmart", "ship_to_id": "addr_home"}


def mint(signer: LocalSigner, ttl: int = 300, **overrides) -> tuple[str, dict]:
    claims = delegated_claims(
        issuer="agentpass",
        audience="agentpass-gateway",
        user_sub="user-1",
        agent_id="agent_shopper_v1",
        sid="sess_1",
        mandate=MANDATE,
        ttl_seconds=ttl,
    )
    claims.update(overrides)
    return encode_jwt(signer, claims), claims


def test_claims_follow_the_contract():
    signer = LocalSigner()
    token, claims = mint(signer)
    verified = TokenVerifier([signer.public_jwk()], "agentpass", "agentpass-gateway").verify(token)
    assert verified == claims
    assert verified["act"] == {"sub": "agent_shopper_v1", "sub_profile": "ai_agent"}
    assert verified["exp"] - verified["iat"] == 300
    assert verified["scope"] == "catalog:read cart:write checkout:execute"
    for key in ("iss", "sub", "aud", "act", "agent_id", "sid", "mandate_id", "tool_profile", "merchant", "ship_to_id", "scope", "jti", "iat", "exp"):
        assert key in verified


def test_expired_token_is_rejected():
    signer = LocalSigner()
    now = int(time.time())
    token, _ = mint(signer, iat=now - 600, exp=now - 300)
    with pytest.raises(TokenError) as exc:
        TokenVerifier([signer.public_jwk()], "agentpass", "agentpass-gateway").verify(token)
    assert exc.value.reason == "token_expired"


def test_wrong_audience_and_tampering_are_rejected():
    signer = LocalSigner()
    verifier = TokenVerifier([signer.public_jwk()], "agentpass", "agentpass-gateway")
    token, _ = mint(signer, aud="someone-else")
    with pytest.raises(TokenError) as exc:
        verifier.verify(token)
    assert exc.value.reason == "invalid_token"

    good, _ = mint(signer)
    header, payload, signature = good.split(".")
    tampered = ".".join([header, payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB"), signature])
    with pytest.raises(TokenError):
        verifier.verify(tampered)


def test_unknown_key_is_rejected():
    token, _ = mint(LocalSigner())
    with pytest.raises(TokenError) as exc:
        TokenVerifier([LocalSigner().public_jwk()], "agentpass", "agentpass-gateway").verify(token)
    assert "unknown signing key" in exc.value.message


def test_unverified_claims_are_for_display_only():
    token, claims = mint(LocalSigner())
    assert unverified_claims(token)["sid"] == claims["sid"]
    assert unverified_claims("not-a-token") == {}
