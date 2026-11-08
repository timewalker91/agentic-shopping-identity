"""The MCP gateway: POST /mcp (MCP over HTTP, JSON responses, stateless).

Every JSON-RPC request runs through the same pipeline and fails closed at each step:

    token  ->  revocation list  ->  session  ->  Cedar policy  ->  budget  ->  store

One audit event is written per tool call (and for every blocked request of any method).
Two policy denials inside the risk window revoke the session (the hijacked-agent demo).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .audit import new_event
from .budget import budget_view, cents_from_amount, dollars
from .clock import now_iso
from .policy import InvalidArguments, validate_args
from .revocation import keys_for_claims, sid_key
from .storage import RevocationError
from .store_client import StoreError
from .tokens import TokenError, unverified_claims

log = logging.getLogger("agentpass.gateway")
router = APIRouter()

PROTOCOL_VERSION = "2025-06-18"
APP_ERROR = -32000
INVALID_PARAMS = -32602
HTTP_FOR_REASON = {
    "invalid_token": 401,
    "token_expired": 401,
    "session_revoked": 401,
    "policy_denied": 403,
    "over_budget": 403,
    "mandate_inactive": 403,
    "invalid_arguments": 400,
    "revocation_check_failed": 503,
    "store_unavailable": 503,
    "gateway_error": 503,
}


class Denied(Exception):
    """Stops the pipeline. Carries what the JSON-RPC error and the audit event need."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        layer: str,
        decision: str = "deny",
        code: int = APP_ERROR,
        data: dict | None = None,
        audited: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.layer = layer
        self.decision = decision
        self.code = code
        self.data = data or {}
        self.audited = audited


@dataclass
class CallContext:
    state: Any
    started: float
    tool: str
    args: Any = field(default_factory=dict)
    sid: str | None = None
    agent_id: str = "unknown"
    user: str = "unknown"

    def audit(self, decision: str, layer: str, reason: str) -> None:
        if not self.sid:
            return
        event = new_event(
            sid=self.sid,
            agent_id=self.agent_id,
            user=self.user,
            tool=self.tool,
            decision=decision,
            reason=reason,
            layer=layer,
            args=self.args,
            latency_ms=(time.perf_counter() - self.started) * 1000,
        )
        try:
            self.state.repo.append_audit(event)
        except Exception:  # an audit outage must not hide the decision from the agent
            log.exception("audit write failed for sid %s", self.sid)


def _rpc_result(rid: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def _rpc_error(rid: Any, status: int, code: int, message: str, data: dict | None = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": error}, status_code=status)


def _tool_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _authenticate(request: Request, ctx: CallContext) -> dict:
    token = _bearer(request)
    if not token:
        raise Denied("invalid_token", "missing bearer token", layer="token")
    try:
        claims = ctx.state.verifier.verify(token)
    except TokenError as exc:
        guess = unverified_claims(token)  # best effort so the failed attempt still shows on the dashboard
        ctx.sid = guess.get("sid")
        ctx.agent_id = guess.get("agent_id", ctx.agent_id)
        raise Denied(exc.reason, exc.message, layer="token") from exc
    ctx.sid = claims["sid"]
    ctx.agent_id = claims["agent_id"]
    ctx.user = claims["sub"]
    return claims


def _check_revocation(ctx: CallContext, claims: dict) -> None:
    try:
        hit = ctx.state.repo.find_revoked(keys_for_claims(claims))
    except RevocationError as exc:
        raise Denied("revocation_check_failed", f"cannot read the revocation list: {exc}", layer="revocation") from exc
    if hit:
        scope = hit["key"].split("#", 1)[0]
        raise Denied(
            "session_revoked",
            f"revoked ({scope}): {hit.get('reason', 'no reason recorded')}",
            layer="revocation",
            decision="revoked",
            data={"revoked_key": hit["key"], "revoked_at": hit.get("ts")},
        )


def _load_session(ctx: CallContext, claims: dict) -> dict:
    session = ctx.state.repo.get_session(claims["sid"])
    if session is None:
        raise Denied("session_revoked", "unknown session", layer="revocation", decision="revoked")
    ctx.user = session.get("username") or claims["sub"]
    if session.get("status") != "active":
        raise Denied(
            "session_revoked",
            f"session is {session.get('status')}: {session.get('revoke_reason', '')}".strip(),
            layer="revocation",
            decision="revoked",
        )
    return session


async def _store_call(ctx: CallContext, name: str, args: dict) -> dict:
    try:
        return await ctx.state.store.call_tool(name, args)
    except StoreError:
        raise
    except Exception as exc:
        log.warning("store unreachable for %s: %s", name, exc.__class__.__name__)
        raise Denied("store_unavailable", "the store MCP server did not answer", layer="gateway") from exc


async def _visible_tools(ctx: CallContext, claims: dict) -> list[dict]:
    try:
        tools = await ctx.state.store.list_tools()
    except Exception as exc:
        raise Denied("store_unavailable", "the store MCP server did not answer", layer="gateway") from exc
    visible = set(ctx.state.policy.visible_tools(claims["tool_profile"]))
    return [t for t in tools if t.get("name") in visible]


def _auto_revoke(ctx: CallContext, claims: dict, count: int) -> None:
    state = ctx.state
    sid = claims["sid"]
    reason = f"auto_revoke: {count} policy denials within {state.risk.window_seconds}s"
    state.repo.revoke(sid_key(sid), reason=reason, by="risk-engine")
    state.repo.update_session(sid, status="revoked", revoked_at=now_iso(), revoke_reason=reason)
    state.risk.forget(sid)
    ctx.audit("revoked", "risk", reason)


async def _checkout(ctx: CallContext, claims: dict, args: dict) -> dict:
    state = ctx.state
    mandate_id = claims["mandate_id"]
    cart = await _store_call(ctx, "view_cart", {"cart_id": args["cart_id"]})
    total_cents = cents_from_amount(cart.get("total", 0))
    held = False
    if total_cents > 0:
        ok, remaining = state.repo.try_hold(mandate_id, total_cents)
        if not ok:
            raise Denied(
                "over_budget",
                f"cart total ${dollars(total_cents):.2f} exceeds the remaining budget ${dollars(remaining):.2f}",
                layer="budget",
                data={"remaining": dollars(remaining), "total": dollars(total_cents)},
            )
        held = True
    try:
        result = await _store_call(ctx, "checkout", args)
    except StoreError as exc:
        if held:
            state.repo.release_hold(mandate_id, total_cents)
        ctx.audit("allow", "budget", f"store_error:{exc.code}")
        return _tool_result({"error": exc.code, "message": exc.message}, is_error=True)
    except Denied:
        if held:
            state.repo.release_hold(mandate_id, total_cents)
        raise
    if result.get("status") == "paid":
        if held:
            state.repo.settle_hold(mandate_id, total_cents)
        reason = f"paid ${dollars(total_cents):.2f}"
    elif result.get("status") == "awaiting_buyer":
        # Shopify handoff: the merchant's checkout is built and the parent pays there. Count it
        # against the mandate now, so an agent cannot stack up payment links beyond the budget.
        if held:
            state.repo.settle_hold(mandate_id, total_cents)
        reason = f"handoff ${dollars(total_cents):.2f}: buyer pays on the merchant checkout"
    else:
        if held:
            state.repo.release_hold(mandate_id, total_cents)
        reason = f"payment_declined:{result.get('reason', 'unknown')}"
    mandate = state.repo.get_mandate(mandate_id)
    if mandate:
        result["budget"] = budget_view(mandate)
    ctx.audit("allow", "budget", reason)
    return _tool_result(result)


async def _tools_call(ctx: CallContext, claims: dict, params: dict) -> dict:
    state = ctx.state
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise Denied("invalid_arguments", "tools/call needs a tool name", layer="policy", code=INVALID_PARAMS)
    ctx.tool = name
    mandate = state.repo.get_mandate(claims["mandate_id"])
    if mandate is None or mandate.get("status") != "active":
        raise Denied("mandate_inactive", "the spending mandate is not active", layer="policy")
    try:
        args = validate_args(name, params.get("arguments") or {})
    except InvalidArguments as exc:
        raise Denied("invalid_arguments", f"invalid arguments: {exc}", layer="policy", code=INVALID_PARAMS) from exc
    ctx.args = args

    decision = state.policy.authorize(claims, name, args)
    if not decision.allowed:
        ctx.audit("deny", "policy", decision.reason)
        count = state.risk.record(claims["sid"])
        data: dict[str, Any] = {"policy": decision.reason, "denials": count}
        if state.risk.should_revoke(count):
            _auto_revoke(ctx, claims, count)
            data["auto_revoked"] = True
        raise Denied("policy_denied", f"policy denied {name}: {decision.reason}", layer="policy", data=data, audited=True)

    if name == "checkout":
        return await _checkout(ctx, claims, args)
    try:
        payload = await _store_call(ctx, name, args)
    except StoreError as exc:
        ctx.audit("allow", "policy", f"store_error:{exc.code}")
        return _tool_result({"error": exc.code, "message": exc.message}, is_error=True)
    ctx.audit("allow", "policy", decision.reason)
    return _tool_result(payload)


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    started = time.perf_counter()
    try:
        body = await request.json()
    except ValueError:
        return _rpc_error(None, 400, -32700, "parse error")
    if not isinstance(body, dict):
        return _rpc_error(None, 400, -32600, "batch requests are not supported")
    rid = body.get("id")
    method = str(body.get("method", ""))
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    ctx = CallContext(state=request.app.state, started=started, tool=method)
    if method == "tools/call":
        ctx.tool = str(params.get("name", "tools/call"))
        ctx.args = params.get("arguments") or {}

    try:
        claims = _authenticate(request, ctx)
        if method.startswith("notifications/"):
            return Response(status_code=202)
        _check_revocation(ctx, claims)
        _load_session(ctx, claims)
        if method == "initialize":
            return _rpc_result(
                rid,
                {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agentpass-gateway", "version": "0.1.0"},
                    "instructions": "Tool calls are checked against your Agent Passport, Cedar policies, "
                    "the revocation list and the spending mandate.",
                },
            )
        if method == "ping":
            return _rpc_result(rid, {})
        if method == "tools/list":
            return _rpc_result(rid, {"tools": await _visible_tools(ctx, claims)})
        if method == "tools/call":
            return _rpc_result(rid, await _tools_call(ctx, claims, params))
        return _rpc_error(rid, 404, -32601, f"method not found: {method}")
    except Denied as denied:
        if not denied.audited:
            ctx.audit(denied.decision, denied.layer, denied.reason if denied.layer == "token" else denied.message)
        data = {"reason": denied.reason, "layer": denied.layer, **denied.data}
        return _rpc_error(rid, HTTP_FOR_REASON.get(denied.reason, 403), denied.code, denied.message, data)
    except Exception:
        log.exception("gateway failure handling %s", method)
        ctx.audit("deny", "gateway", "gateway_error")
        return _rpc_error(rid, 503, APP_ERROR, "gateway error", {"reason": "gateway_error", "layer": "gateway"})
