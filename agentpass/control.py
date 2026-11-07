"""Control API for the dashboard: login, mandates, sessions, Stop, audit feed, demo reset.

Every route except /auth/login needs the user's Cognito access token (Bearer).
"""

from __future__ import annotations

import secrets
import time
from decimal import Decimal

from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .audit import record
from .aws import cognito, table
from .cognito_auth import current_user
from .config import AGENTS, COGNITO_CLIENT_ID, T_AUDIT, T_MANDATES, T_REVOCATIONS, T_SESSIONS
from .revocation import revoke

router = APIRouter()

DEMO_MANDATE_ID = "mdt_42"


def plain(item: dict) -> dict:
    """DynamoDB returns Decimal; turn numbers into int/float for JSON."""
    def conv(v):
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v
    return conv(item)


class Login(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: Login) -> dict:
    """Log the human in through Cognito (the browser never calls AWS directly)."""
    try:
        res = cognito().initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": body.username, "PASSWORD": body.password},
        )["AuthenticationResult"]
    except cognito().exceptions.NotAuthorizedException as e:
        raise HTTPException(401, "wrong username or password") from e
    return {"access_token": res["AccessToken"], "expires_in": res["ExpiresIn"]}


@router.get("/mandates/{mandate_id}")
def get_mandate(mandate_id: str, user: dict = Depends(current_user)) -> dict:
    m = table(T_MANDATES).get_item(Key={"mandate_id": mandate_id}, ConsistentRead=True).get("Item")
    if not m or m["user_id"] != user["sub"]:
        raise HTTPException(404, "mandate not found")
    m = plain(m)
    # Budget fields in dollars for the dashboard (stored in cents).
    return {**m, "max": m["max_amount"] / 100, "spent": m["spent"] / 100, "held": m["held"] / 100}


class NewSession(BaseModel):
    mandate_id: str = DEMO_MANDATE_ID
    agent_id: str = "agent_shopper_v1"


@router.post("/sessions")
def create_session(body: NewSession, user: dict = Depends(current_user)) -> dict:
    if body.agent_id not in AGENTS:
        raise HTTPException(400, "unknown agent")
    m = table(T_MANDATES).get_item(Key={"mandate_id": body.mandate_id}, ConsistentRead=True).get("Item")
    if not m or m["user_id"] != user["sub"] or m["status"] != "active":
        raise HTTPException(400, "mandate not active for this user")
    sid = f"sess_{secrets.token_hex(6)}"
    table(T_SESSIONS).put_item(Item={
        "sid": sid, "user_id": user["sub"], "agent_id": body.agent_id, "mandate_id": body.mandate_id,
        "status": "active", "violations": 0, "created_at": int(time.time()),
    })
    record(sid, agent_id=body.agent_id, user=user["sub"], tool="session", decision="allow",
           reason="session created", layer="token")
    return {"sid": sid}


@router.post("/sessions/{sid}/stop")
def stop_session(sid: str, user: dict = Depends(current_user)) -> dict:
    s = table(T_SESSIONS).get_item(Key={"sid": sid}, ConsistentRead=True).get("Item")
    if not s or s["user_id"] != user["sub"]:
        raise HTTPException(404, "session not found")
    revoke(f"sid#{sid}", level="session", reason="stopped by user", actor=user["sub"])
    table(T_SESSIONS).update_item(
        Key={"sid": sid}, UpdateExpression="SET #s = :r", ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":r": "revoked"},
    )
    event = record(sid, agent_id=s["agent_id"], user=user["sub"], tool="stop", decision="revoked",
                   reason="session revoked by user (Stop)", layer="revocation")
    return {"revoked": True, "at": event["ts"]}


@router.get("/sessions/{sid}/audit")
def audit_feed(sid: str, after: str = "", user: dict = Depends(current_user)) -> dict:
    s = table(T_SESSIONS).get_item(Key={"sid": sid}).get("Item")
    if not s or s["user_id"] != user["sub"]:
        raise HTTPException(404, "session not found")
    cond = Key("sid").eq(sid) & Key("sk").gt(after) if after else Key("sid").eq(sid)
    items = table(T_AUDIT).query(KeyConditionExpression=cond, ConsistentRead=True)["Items"]
    return {"sid": sid, "status": s["status"], "events": [plain(i) for i in items]}


@router.post("/demo/reset")
def demo_reset(user: dict = Depends(current_user)) -> dict:
    """Clear sessions, revocations and audit, then seed the demo mandate for this user.
    The store's catalog and carts are reset by the store owner (Larry)."""
    for name, keys in ((T_SESSIONS, ["sid"]), (T_REVOCATIONS, ["key"]), (T_AUDIT, ["sid", "sk"])):
        t = table(name)
        scan = t.scan(ProjectionExpression=", ".join(f"#k{i}" for i in range(len(keys))),
                      ExpressionAttributeNames={f"#k{i}": k for i, k in enumerate(keys)})
        with t.batch_writer() as batch:
            for item in scan["Items"]:
                batch.delete_item(Key={k: item[k] for k in keys})
    table(T_MANDATES).put_item(Item={
        "mandate_id": DEMO_MANDATE_ID, "user_id": user["sub"], "agent_id": "agent_shopper_v1",
        "merchant": "freshmart", "categories": ["grocery", "school"],
        "max_amount": 15000, "spent": 0, "held": 0, "holds": {},  # cents: $150 budget
        "step_up_above": 10000, "ship_to_id": "addr_home",
        "expires_at": int(time.time()) + 12 * 3600, "status": "active",
    })
    return {"reset": True, "mandate_id": DEMO_MANDATE_ID}
