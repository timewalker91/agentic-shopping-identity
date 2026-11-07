"""Token signing keys behind one interface.

- LocalSigner: an EC P-256 key generated at startup (local mode, tests).
- KmsSigner:   an AWS KMS asymmetric key (ECC_NIST_P256, SIGN_VERIFY). The private key never
               leaves KMS; we send the SHA-256 digest and get a DER signature back.

Both produce ES256 JWS signatures and publish the public key as a JWK for /.well-known/jwks.json.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ES256 = "ES256"


class Signer(Protocol):
    kid: str
    alg: str

    def sign(self, signing_input: bytes) -> bytes: ...
    def public_jwk(self) -> dict: ...


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _der_to_raw(der_signature: bytes) -> bytes:
    """JWS wants r||s (32 bytes each for P-256); KMS and cryptography return DER."""
    r, s = decode_dss_signature(der_signature)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _public_jwk(public_key: ec.EllipticCurvePublicKey, kid: str) -> dict:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(numbers.x.to_bytes(32, "big")),
        "y": _b64url(numbers.y.to_bytes(32, "big")),
        "kid": kid,
        "use": "sig",
        "alg": ES256,
    }


def _kid_for(public_key: ec.EllipticCurvePublicKey, prefix: str) -> str:
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return f"{prefix}-{hashlib.sha256(der).hexdigest()[:16]}"


class LocalSigner:
    alg = ES256

    def __init__(self, private_key: ec.EllipticCurvePrivateKey | None = None) -> None:
        self._key = private_key or ec.generate_private_key(ec.SECP256R1())
        self._public = self._key.public_key()
        self.kid = _kid_for(self._public, "local")

    def sign(self, signing_input: bytes) -> bytes:
        return _der_to_raw(self._key.sign(signing_input, ec.ECDSA(hashes.SHA256())))

    def public_jwk(self) -> dict:
        return _public_jwk(self._public, self.kid)


class KmsSigner:
    alg = ES256

    def __init__(self, key_id: str, region: str) -> None:
        import boto3

        self._kms = boto3.client("kms", region_name=region)
        self._key_id = key_id
        resp = self._kms.get_public_key(KeyId=key_id)
        if resp.get("KeySpec") != "ECC_NIST_P256":
            raise ValueError(f"KMS key {key_id} must be ECC_NIST_P256, got {resp.get('KeySpec')}")
        public = serialization.load_der_public_key(resp["PublicKey"])
        if not isinstance(public, ec.EllipticCurvePublicKey):
            raise ValueError("KMS public key is not an EC key")
        self._public = public
        self.kid = _kid_for(public, "kms")

    def sign(self, signing_input: bytes) -> bytes:
        digest = hashlib.sha256(signing_input).digest()
        resp = self._kms.sign(
            KeyId=self._key_id, Message=digest, MessageType="DIGEST", SigningAlgorithm="ECDSA_SHA_256"
        )
        return _der_to_raw(resp["Signature"])

    def public_jwk(self) -> dict:
        return _public_jwk(self._public, self.kid)


def jwk_json(jwk: dict) -> str:
    return json.dumps(jwk)
