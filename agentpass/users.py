"""User (parent) authentication for the Control API and the token exchange.

- CognitoUserAuth: Amazon Cognito user pool. Login = USER_PASSWORD_AUTH via boto3 (server side,
  so the dashboard never talks to AWS). Verification = Cognito's JWKS (RS256).
- LocalUserAuth:   local mode. Users come from AGENTPASS_LOCAL_USERS; the id token is signed with
                   the local signer so the rest of the code is identical.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass

import jwt
from jwt.algorithms import ECAlgorithm

from .clock import now_epoch
from .signer import Signer
from .tokens import encode_jwt

LOCAL_ISSUER = "agentpass-local-idp"
LOCAL_AUDIENCE = "agentpass"
ID_TOKEN_TTL = 3600


class UserAuthError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class UserIdentity:
    sub: str
    username: str


@dataclass(frozen=True)
class LoginResult:
    id_token: str
    expires_in: int
    identity: UserIdentity


class LocalUserAuth:
    def __init__(self, users: dict[str, str], signer: Signer) -> None:
        self._users = users
        self._signer = signer
        self._key = ECAlgorithm.from_jwk(json.dumps(signer.public_jwk()))

    def login(self, username: str, password: str) -> LoginResult:
        expected = self._users.get(username)
        if expected is None or not hmac.compare_digest(expected, password):
            raise UserAuthError("invalid_credentials", "unknown user or wrong password")
        now = now_epoch()
        sub = str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentpass:user:{username}"))
        claims = {
            "iss": LOCAL_ISSUER,
            "aud": LOCAL_AUDIENCE,
            "sub": sub,
            "cognito:username": username,
            "token_use": "id",
            "iat": now,
            "exp": now + ID_TOKEN_TTL,
        }
        return LoginResult(encode_jwt(self._signer, claims), ID_TOKEN_TTL, UserIdentity(sub, username))

    def verify(self, id_token: str) -> UserIdentity:
        try:
            claims = jwt.decode(id_token, self._key, algorithms=["ES256"], audience=LOCAL_AUDIENCE, issuer=LOCAL_ISSUER)
        except jwt.InvalidTokenError as exc:
            raise UserAuthError("invalid_user_token", f"user token rejected: {exc}") from exc
        if claims.get("token_use") != "id":
            raise UserAuthError("invalid_user_token", "not an id token")
        return UserIdentity(claims["sub"], claims.get("cognito:username", claims["sub"]))


class CognitoUserAuth:
    def __init__(self, region: str, user_pool_id: str, client_id: str, client_secret: str | None = None) -> None:
        import boto3

        self._idp = boto3.client("cognito-idp", region_name=region)
        self._client_id = client_id
        self._client_secret = client_secret
        self._issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self._jwks = jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json", cache_keys=True)

    def _secret_hash(self, username: str) -> str:
        digest = hmac.new(
            self._client_secret.encode(), (username + self._client_id).encode(), hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode()

    def login(self, username: str, password: str) -> LoginResult:
        from botocore.exceptions import ClientError

        params = {"USERNAME": username, "PASSWORD": password}
        if self._client_secret:
            params["SECRET_HASH"] = self._secret_hash(username)
        try:
            resp = self._idp.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH", ClientId=self._client_id, AuthParameters=params
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("NotAuthorizedException", "UserNotFoundException"):
                raise UserAuthError("invalid_credentials", "unknown user or wrong password") from exc
            raise UserAuthError("cognito_error", f"Cognito login failed: {code}") from exc
        if "ChallengeName" in resp:
            raise UserAuthError(
                "challenge_required",
                f"Cognito asks for {resp['ChallengeName']}; set a permanent password with infra/setup.py",
            )
        result = resp["AuthenticationResult"]
        identity = self.verify(result["IdToken"])
        return LoginResult(result["IdToken"], int(result.get("ExpiresIn", 3600)), identity)

    def verify(self, id_token: str) -> UserIdentity:
        try:
            key = self._jwks.get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=self._client_id, issuer=self._issuer)
        except jwt.PyJWTError as exc:
            raise UserAuthError("invalid_user_token", f"user token rejected: {exc}") from exc
        if claims.get("token_use") != "id":
            raise UserAuthError("invalid_user_token", "not an id token")
        username = claims.get("cognito:username") or claims.get("email") or claims["sub"]
        return UserIdentity(claims["sub"], username)
