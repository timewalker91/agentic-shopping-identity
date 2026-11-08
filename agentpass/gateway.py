"""Aide-commerce MCP gateway: POST /mcp. The only path from the agent to the store.

Every tools/call goes through, in order:
  1. Token     - delegated token signed by Aide-commerce (KMS), right audience, not expired
  2. Revoked?  - sid#, agent#, user#, mandate#, jti# in the revocation list (fail closed)
  3. Mandate   - still active and unexpired
  4. Cedar     - policies/shopper.cedar on the tool name + arguments (default deny)
  5. Budget    - checkout only: hold the server-side cart total against the mandate
  then forwards to the store, commits/releases the budget hold, and writes an audit event.
Two policy denials in one session revoke it automatically (risk rule).

Decisions come back as JSON-RPC errors (code -32000, reason in error.data.reason,
error.data.status = 401/403) over HTTP 200, so MCP client SDKs surface them as McpError.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cedarpy
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .audit import record
from .aws import table
from .config import AUDIENCE, ISSUER, STORE_MCP_URL, T_MANDATES, T_SESSIONS, T_STORE, TOKEN_SIGNER_KEY_ARN
from .kms_jwt import verify_jwt
from .revocation import RevocationLookupError, revoke, revoked

router = APIRouter()

POLICIES = (Path(__file__).parent / "policies" / "shopper.cedar").read_text()
POLICY_NAMES = {  # cedarpy names policies by position in the file
    "policy0": "permit-read-tools", "policy1": "permit-add-to-cart",
    "policy2": "forbid-gift-cards", "policy3": "permit-checkout", "policy4": "forbid-high-risk-tools",
}
VISIBLE_TOOLS = {  # tools/list per tool profile; high-risk tools are never shown
    "shopper_v1": {"search_products", "get_product", "view_cart", "add_to_cart", "checkout", "get_order_status"},
}
VIOLATIONS_TO_REVOKE = 2


class Denied(Exception):
    def __init__(self, status: int, reason: str, layer: str, **extra):
        super().__init__(reason)
        self.status, self.reason, self.layer, self.extra = status, reason, layer, extra


def rpc_result(rid, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def rpc_error(rid, status: int, reason: str, **extra) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {
        "code": -32000, "message": reason, "data": {"reason": reason, "status": status, **extra}}})


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    msg = await request.json()
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}

    if rid is None:  # notifications (e.g. notifications/initialized) get no response body
        return Response(status_code=202)
    if method == "initialize":
        return rpc_result(rid, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "agentpass-gateway", "version": "0.1.0"},
        })
    if method == "ping":
        return rpc_result(rid, {})

    # Everything else needs a valid delegated token.
    auth = request.headers.get("authorization", "")
    try:
        claims = verify_jwt(auth.removeprefix("Bearer "), TOKEN_SIGNER_KEY_ARN, audience=AUDIENCE, issuer=ISSUER,
                            require=["exp", "iat", "jti", "sub", "sid"])
    except jwt.PyJWTError as e:
        return JSONResponse(status_code=401, content={"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32000, "message": "invalid_token", "data": {"reason": "invalid_token", "status": 401, "detail": str(e)}}})

    if method == "tools/list":
        return await list_tools(rid, claims)
    if method == "tools/call":
        return await call_tool(rid, claims, params.get("name", ""), params.get("arguments") or {})
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}})


@asynccontextmanager
async def store_session():
    async with streamablehttp_client(STORE_MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_tools(rid, claims: dict) -> JSONResponse:
    try:
        check_revocation(claims)
    except Denied as d:
        return rpc_error(rid, d.status, d.reason)
    async with store_session() as store:
        tools = (await store.list_tools()).tools
    visible = VISIBLE_TOOLS.get(claims.get("tool_profile"), set())
    return rpc_result(rid, {"tools": [t.model_dump(by_alias=True, exclude_none=True, mode="json")
                                      for t in tools if t.name in visible]})


async def call_tool(rid, claims: dict, tool: str, args: dict) -> JSONResponse:
    started = time.perf_counter()
    sid, agent_id, user = claims["sid"], claims.get("agent_id", "?"), claims["sub"]

    def audit(decision: str, reason: str, layer: str) -> None:
        record(sid, agent_id=agent_id, user=user, tool=tool, decision=decision, reason=reason, layer=layer,
               args=args, latency_ms=int((time.perf_counter() - started) * 1000))

    hold = None
    try:
        check_revocation(claims)
        mandate = check_mandate(claims)
        check_policy(claims, tool, args)
        if tool == "checkout":
            hold = place_budget_hold(mandate, claims, args, rid)
    except Denied as d:
        audit("revoked" if d.status == 401 else "deny", d.reason, d.layer)
        if d.layer == "policy":
            auto_revoke_if_needed(sid, agent_id, user)
        return rpc_error(rid, d.status, d.reason, **d.extra)

    async with store_session() as store:
        result = await store.call_tool(tool, args)
    if hold:
        settle_budget_hold(claims["mandate_id"], hold, success=not result.isError)
    audit("allow", "ok" if not result.isError else "store returned an error", "policy")
    return rpc_result(rid, result.model_dump(by_alias=True, exclude_none=True, mode="json"))


def check_revocation(claims: dict) -> None:
    keys = [f"sid#{claims['sid']}", f"agent#{claims.get('agent_id')}", f"user#{claims['sub']}",
            f"mandate#{claims.get('mandate_id')}", f"jti#{claims['jti']}"]
    try:
        hits = revoked(keys)
    except RevocationLookupError:
        raise Denied(401, "revocation_check_unavailable", "revocation")  # fail closed
    if hits:
        raise Denied(401, "session_revoked", "revocation", revoked_key=hits[0].split("#")[0])


def check_mandate(claims: dict) -> dict:
    m = table(T_MANDATES).get_item(Key={"mandate_id": claims.get("mandate_id", "")}, ConsistentRead=True).get("Item")
    if not m or m.get("status") != "active" or int(m["expires_at"]) <= time.time():
        raise Denied(403, "mandate_inactive", "revocation")
    return m


def check_policy(claims: dict, tool: str, args: dict) -> None:
    entities = [
        {"uid": {"type": "Agent", "id": claims.get("agent_id", "")}, "parents": [], "attrs": {
            "tool_profile": claims.get("tool_profile", ""), "merchant": claims.get("merchant", ""),
            "ship_to_id": claims.get("ship_to_id", "")}},
        {"uid": {"type": "Gateway", "id": "agentpass"}, "attrs": {}, "parents": []},
    ]
    # Cedar has no floats or nulls: pass ints/bools/strings as-is, everything else as text.
    context = {"input": {k: v if isinstance(v, (int, bool, str)) else json.dumps(v) for k, v in args.items()}}
    result = cedarpy.is_authorized({
        "principal": f'Agent::"{claims.get("agent_id", "")}"', "action": f'Action::"{tool}"',
        "resource": 'Gateway::"agentpass"', "context": context}, POLICIES, entities)
    if result.decision != cedarpy.Decision.Allow:
        names = [POLICY_NAMES.get(p, p) for p in result.diagnostics.reasons] or ["default-deny"]
        raise Denied(403, "policy_denied", "policy", policies=names)


def place_budget_hold(mandate: dict, claims: dict, args: dict, rid) -> dict:
    """Hold the cart total (computed by the store, read from DynamoDB) before checkout runs."""
    cart = table(T_STORE).get_item(Key={"pk": f"cart#{args.get('cart_id', '')}"}, ConsistentRead=True).get("Item")
    if not cart:
        raise Denied(403, "cart_not_found", "budget")
    total = int(cart["total"])
    spent, held, limit = int(mandate["spent"]), int(mandate["held"]), int(mandate["max_amount"])
    if spent + held + total > limit:
        raise Denied(403, "over_budget", "budget", remaining=round((limit - spent - held) / 100, 2),
                     cart_total=round(total / 100, 2))
    key = hashlib.sha256(f"{claims['jti']}:{rid}".encode()).hexdigest()[:16]
    try:  # only succeeds if nobody else moved the budget since we read it
        table(T_MANDATES).update_item(
            Key={"mandate_id": mandate["mandate_id"]},
            UpdateExpression="SET held = :new_held, holds.#k = :t",
            ConditionExpression="#st = :active AND spent = :s AND held = :h AND attribute_not_exists(holds.#k)",
            ExpressionAttributeNames={"#k": key, "#st": "status"},
            ExpressionAttributeValues={":new_held": held + total, ":t": total, ":s": spent, ":h": held, ":active": "active"},
        )
    except table(T_MANDATES).meta.client.exceptions.ConditionalCheckFailedException:
        raise Denied(403, "over_budget", "budget", detail="budget changed concurrently, retry")
    return {"key": key, "amount": total}


def settle_budget_hold(mandate_id: str, hold: dict, *, success: bool) -> None:
    move = "spent = spent + :t, held = held - :t" if success else "held = held - :t"
    table(T_MANDATES).update_item(
        Key={"mandate_id": mandate_id}, UpdateExpression=f"SET {move} REMOVE holds.#k",
        ExpressionAttributeNames={"#k": hold["key"]}, ExpressionAttributeValues={":t": hold["amount"]},
    )


def auto_revoke_if_needed(sid: str, agent_id: str, user: str) -> None:
    """Risk rule: repeated policy denials mean the agent is likely hijacked -> revoke the session."""
    count = int(table(T_SESSIONS).update_item(
        Key={"sid": sid}, UpdateExpression="ADD violations :one",
        ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW",
    )["Attributes"]["violations"])
    if count >= VIOLATIONS_TO_REVOKE:
        revoke(f"sid#{sid}", level="session", reason=f"auto-revoked after {count} policy denials", actor="risk-engine")
        table(T_SESSIONS).update_item(Key={"sid": sid}, UpdateExpression="SET #s = :r",
                                      ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":r": "revoked"})
        record(sid, agent_id=agent_id, user=user, tool="risk-engine", decision="revoked",
               reason=f"session auto-revoked after {count} policy denials (likely hijacked agent)", layer="risk")
