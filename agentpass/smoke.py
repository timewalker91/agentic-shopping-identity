"""Smoke test for the token service + Control API against a running server.

  Terminal 1:  uv run uvicorn agentpass.main:app --port 8000
  Terminal 2:  uv run python -m agentpass.smoke

It plays the credential broker's part: signs a client assertion with the agent's KMS key.
Prints claims only, never full tokens.
"""

from __future__ import annotations

import os
import time
import uuid

import httpx
import jwt

from .config import AGENTS, AUDIENCE, BASE_URL, ISSUER, TOKEN_ENDPOINT
from .kms_jwt import sign_jwt

AGENT_ID = "agent_shopper_v1"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -> ' + detail) if detail else ''}")


def assertion() -> str:
    now = int(time.time())
    return sign_jwt(AGENTS[AGENT_ID]["key_arn"], {
        "iss": AGENT_ID, "sub": AGENT_ID, "aud": TOKEN_ENDPOINT,
        "iat": now, "exp": now + 60, "jti": str(uuid.uuid4()),
    })


def exchange(c: httpx.Client, user_token: str, sid: str, actor: str) -> httpx.Response:
    return c.post("/oauth2/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": user_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "actor_token": actor,
        "actor_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "audience": AUDIENCE,
        "scope": "catalog:read cart:write checkout:execute",
        "session_id": sid,
    })


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=20) as c:
        r = c.post("/auth/login", json={"username": os.environ["DEMO_USERNAME"], "password": os.environ["DEMO_PASSWORD"]})
        check("login through Cognito", r.status_code == 200, str(r.status_code))
        user_token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {user_token}"}

        r = c.post("/demo/reset", headers=auth)
        check("demo reset seeds mandate", r.status_code == 200, r.text[:80])
        r = c.post("/sessions", headers=auth, json={})
        check("create session", r.status_code == 200, r.text[:80])
        sid = r.json()["sid"]

        actor = assertion()
        r = exchange(c, user_token, sid, actor)
        check("token exchange issues delegated token", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
        if r.status_code == 200:
            jwks = jwt.PyJWKSet.from_dict(c.get("/.well-known/jwks.json").json())
            claims = jwt.decode(r.json()["access_token"], jwks.keys[0].key, algorithms=["RS256"], audience=AUDIENCE, issuer=ISSUER)
            check("token verifies with public JWKS", True,
                  f"sub={claims['sub'][:8]}… act={claims['act']['sub']} sid={claims['sid']} "
                  f"mandate={claims['mandate_id']} profile={claims['tool_profile']} ttl={claims['exp'] - claims['iat']}s")

        r = exchange(c, user_token, sid, actor)
        check("replayed assertion rejected", r.status_code == 400 and "replayed" in r.text, r.text[:100])

        r = c.post(f"/sessions/{sid}/stop", headers=auth)
        check("Stop revokes session", r.status_code == 200, r.text[:80])
        r = exchange(c, user_token, sid, assertion())
        check("exchange after Stop rejected", r.status_code == 400 and "revoked" in r.text, r.text[:100])

        r = c.get(f"/sessions/{sid}/audit", headers=auth)
        events = r.json().get("events", [])
        check("audit feed records decisions", len(events) >= 4,
              " | ".join(f"{e['tool']}:{e['decision']}" for e in events))

    passed = sum(ok for _, ok in results)
    print(f"\n{passed}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
