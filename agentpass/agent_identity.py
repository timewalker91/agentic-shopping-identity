"""Agent authentication: the agent proves who it is with its own key (RFC 7523 client assertion).

The credential broker signs a short-lived JWT with the agent's private key and sends it as the
`actor_token` of the token exchange. The token service only issues an Agent Passport if:

  - the assertion is signed by the key registered for that agent (KMS in aws mode),
  - iss = sub = the agent id, aud = the token service, and it lives at most 60 seconds,
  - its jti has never been seen before (single use: a copied assertion can't be replayed).

Keys:
  aws mode   -> an AWS KMS key (ECC_NIST_P256). The private key never leaves KMS; the broker
                calls kms:Sign and the token service reads the public key with kms:GetPublicKey.
  local mode -> a P-256 key in a PEM file (default .local/agent_shopper_v1.pem, gitignored),
                created on first use and shared by the broker and the server on one machine.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from .clock import now_epoch
from .config import REPO_ROOT
from .signer import ES256, KmsSigner, LocalSigner, Signer
from .tokens import encode_jwt

ACTOR_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
ASSERTION_AUDIENCE = "agentpass-token-service"
ASSERTION_MAX_LIFETIME = 60  # seconds
ASSERTION_REPLAY_WINDOW = 600  # remember used jti values this long (longer than any assertion lives)
DEFAULT_AGENT_KEY_FILE = REPO_ROOT / ".local" / "agent_shopper_v1.pem"


class AgentAuthError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# -- keys -----------------------------------------------------------------------------------
def local_agent_signer(path: Path | str | None = None) -> LocalSigner:
    """Load the agent's local P-256 key, creating it (mode 0600) on first use."""
    path = Path(path or os.environ.get("AGENTPASS_AGENT_KEY_FILE") or DEFAULT_AGENT_KEY_FILE)
    if path.is_file():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError(f"{path} is not an EC private key")
        return LocalSigner(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return LocalSigner(key)


def agent_signer_from_env(region: str = "us-east-1") -> Signer:
    """The broker's signer: the agent's KMS key if AGENTPASS_AGENT_KMS_KEY_ID is set, else the local key file."""
    kms_key = os.environ.get("AGENTPASS_AGENT_KMS_KEY_ID")
    if kms_key:
        return KmsSigner(kms_key, os.environ.get("AWS_REGION", region))
    return local_agent_signer()


# -- broker side ------------------------------------------------------------------------------
def make_assertion(signer: Signer, agent_id: str, lifetime: int = ASSERTION_MAX_LIFETIME) -> str:
    now = now_epoch()
    claims = {
        "iss": agent_id,
        "sub": agent_id,
        "aud": ASSERTION_AUDIENCE,
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_urlsafe(16),
    }
    return encode_jwt(signer, claims)


# -- token service side ----------------------------------------------------------------------
class AgentRegistry:
    """Registered agents and their public keys (JWK). Only these agents can get a passport."""

    def __init__(self, agents: dict[str, dict]) -> None:
        self._keys = {agent_id: ECAlgorithm.from_jwk(json.dumps(jwk)) for agent_id, jwk in agents.items()}
        self._kids = {agent_id: jwk.get("kid") for agent_id, jwk in agents.items()}

    def kid(self, agent_id: str) -> str | None:
        return self._kids.get(agent_id)

    def verify(self, assertion: str, use_once) -> str:
        """Return the authenticated agent id, or raise AgentAuthError.

        `use_once(key, ttl_seconds) -> bool` records the jti; False means it was already used.
        """
        try:
            unverified = jwt.decode(assertion, options={"verify_signature": False})
        except jwt.InvalidTokenError as exc:
            raise AgentAuthError("invalid_actor_token", f"actor_token is not a signed JWT: {exc}") from exc
        agent_id = str(unverified.get("iss", ""))
        key = self._keys.get(agent_id)
        if key is None:
            raise AgentAuthError("unknown_agent", f"agent {agent_id or '?'} is not registered")
        try:
            claims = jwt.decode(
                assertion,
                key,
                algorithms=[ES256],
                audience=ASSERTION_AUDIENCE,
                issuer=agent_id,
                leeway=5,
                options={"require": ["iss", "sub", "aud", "iat", "exp", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AgentAuthError("actor_token_expired", "agent assertion expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AgentAuthError("invalid_actor_token", f"agent assertion rejected: {exc}") from exc
        if claims["sub"] != agent_id:
            raise AgentAuthError("invalid_actor_token", "assertion sub must equal iss (the agent id)")
        if int(claims["exp"]) - int(claims["iat"]) > ASSERTION_MAX_LIFETIME:
            raise AgentAuthError("invalid_actor_token", f"assertion must live at most {ASSERTION_MAX_LIFETIME}s")
        if not use_once(f"assertion#{claims['jti']}", ASSERTION_REPLAY_WINDOW):
            raise AgentAuthError("actor_token_replayed", "this agent assertion was already used")
        return agent_id
