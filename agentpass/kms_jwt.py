"""Sign and verify RS256 JWTs with KMS keys. The private keys never leave KMS."""

from __future__ import annotations

import base64
import hashlib
import json
from functools import cache

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .aws import kms


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def kid(key_arn: str) -> str:
    return key_arn.rsplit("/", 1)[-1]


def sign_jwt(key_arn: str, claims: dict) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": kid(key_arn)}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
    )
    digest = hashlib.sha256(signing_input.encode()).digest()
    signature = kms().sign(
        KeyId=key_arn,
        Message=digest,
        MessageType="DIGEST",
        SigningAlgorithm="RSASSA_PKCS1_V1_5_SHA_256",
    )["Signature"]
    return f"{signing_input}.{_b64url(signature)}"


@cache
def public_key(key_arn: str) -> RSAPublicKey:
    der = kms().get_public_key(KeyId=key_arn)["PublicKey"]
    return load_der_public_key(der)


def jwk(key_arn: str) -> dict:
    numbers = public_key(key_arn).public_numbers()

    def to_b64(n: int) -> str:
        return _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))

    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid(key_arn), "n": to_b64(numbers.n), "e": to_b64(numbers.e)}


def verify_jwt(token: str, key_arn: str, *, audience: str, issuer: str, require: list[str]) -> dict:
    return jwt.decode(
        token,
        public_key(key_arn),
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
        leeway=5,
        options={"require": require},
    )
