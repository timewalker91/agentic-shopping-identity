"""Control API (plan, section 5): login, sessions, Stop, audit feed, mandates, demo reset.

Every route except /auth/login needs the user's id token (Cognito in aws mode) as a bearer.
The dashboard talks only to these routes; AWS calls happen here, server side.
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .audit import new_event
from .budget import budget_view, cents_from_amount, mandate_view
from .clock import now_iso
from .revocation import sid_key
from .users import UserAuthError, UserIdentity

router = APIRouter()


def _http_error(status: int, reason: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": reason, "message": message})


def require_user(request: Request) -> UserIdentity:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _http_error(401, "login_required", "send the user's id token as a bearer token")
    try:
        return request.app.state.user_auth.verify(token.strip())
    except UserAuthError as exc:
        raise _http_error(401, exc.reason, exc.message) from exc


def new_mandate(
    mandate_id: str, identity: UserIdentity, *, max_cents: int, merchant: str, ship_to_id: str, tool_profile: str
) -> dict:
    return {
        "mandate_id": mandate_id,
        "user": identity.sub,
        "username": identity.username,
        "merchant": merchant,
        "ship_to_id": ship_to_id,
        "tool_profile": tool_profile,
        "status": "active",
        "currency": "USD",
        "max_cents": max_cents,
        "spent_cents": 0,
        "held_cents": 0,
        "created_at": now_iso(),
    }


def ensure_demo_mandate(state: Any, identity: UserIdentity) -> list[dict]:
    """Every user gets a demo mandate at first login so the demo needs no mandate form."""
    mandates = state.repo.list_mandates_for_user(identity.sub)
    if mandates:
        return mandates
    settings = state.settings
    mandate_id = "mdt_demo" if state.repo.get_mandate("mdt_demo") is None else f"mdt_{secrets.token_hex(4)}"
    mandate = new_mandate(
        mandate_id,
        identity,
        max_cents=settings.demo_budget_cents,
        merchant=settings.demo_merchant,
        ship_to_id=settings.demo_ship_to_id,
        tool_profile=settings.tool_profile,
    )
    state.repo.put_mandate(mandate)
    return [mandate]


def session_view(session: dict, state: Any) -> dict:
    view = {k: v for k, v in session.items() if k not in ("last_jti",)}
    mandate = state.repo.get_mandate(session["mandate_id"])
    if mandate:
        view["budget"] = budget_view(mandate)
    return view


def _owned_session(sid: str, identity: UserIdentity, state: Any) -> dict:
    session = state.repo.get_session(sid)
    if session is None or session.get("user") != identity.sub:
        raise _http_error(404, "unknown_session", f"no session {sid} for this user")
    return session


def _owned_mandate(mandate_id: str, identity: UserIdentity, state: Any) -> dict:
    mandate = state.repo.get_mandate(mandate_id)
    if mandate is None or mandate.get("user") != identity.sub:
        raise _http_error(404, "unknown_mandate", f"no mandate {mandate_id} for this user")
    return mandate


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/auth/login")
def login(body: LoginBody, request: Request) -> dict:
    state = request.app.state
    try:
        result = state.user_auth.login(body.username, body.password)
    except UserAuthError as exc:
        raise _http_error(401, exc.reason, exc.message) from exc
    mandates = ensure_demo_mandate(state, result.identity)
    return {
        "id_token": result.id_token,
        "token_type": "Bearer",
        "expires_in": result.expires_in,
        "user": {"sub": result.identity.sub, "username": result.identity.username},
        "mandates": [mandate_view(m) for m in mandates],
    }


@router.get("/me")
def me(request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    return {
        "user": {"sub": identity.sub, "username": identity.username},
        "mandates": [mandate_view(m) for m in ensure_demo_mandate(state, identity)],
        "sessions": [session_view(s, state) for s in state.repo.list_sessions_for_user(identity.sub)[:10]],
    }


class SessionBody(BaseModel):
    mandate_id: str = Field(min_length=1)


@router.post("/sessions", status_code=201)
def create_session(body: SessionBody, request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    mandate = _owned_mandate(body.mandate_id, identity, state)
    if mandate.get("status") != "active":
        raise _http_error(403, "mandate_inactive", "the mandate is not active")
    session = {
        "sid": f"sess_{secrets.token_hex(6)}",
        "user": identity.sub,
        "username": identity.username,
        "mandate_id": mandate["mandate_id"],
        "agent_id": state.settings.agent_id,
        "tool_profile": mandate["tool_profile"],
        "merchant": mandate["merchant"],
        "status": "active",
        "created_at": now_iso(),
        "passport": None,
    }
    state.repo.put_session(session)
    return session_view(session, state)


@router.get("/sessions")
def list_sessions(request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    return {"sessions": [session_view(s, state) for s in state.repo.list_sessions_for_user(identity.sub)]}


@router.get("/sessions/{sid}")
def get_session(sid: str, request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    view = session_view(_owned_session(sid, identity, state), state)
    view["revocation"] = state.repo.find_revoked([sid_key(sid)])
    return view


@router.post("/sessions/{sid}/stop")
def stop_session(sid: str, request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    session = _owned_session(sid, identity, state)
    if session.get("status") == "active":
        reason = f"user_stop: Stop pressed by {identity.username}"
        state.repo.revoke(sid_key(sid), reason=reason, by=identity.username)
        session = state.repo.update_session(sid, status="revoked", revoked_at=now_iso(), revoke_reason=reason)
        state.risk.forget(sid)
        state.repo.append_audit(
            new_event(
                sid=sid,
                agent_id=session["agent_id"],
                user=identity.username,
                tool="stop",
                decision="revoked",
                reason=reason,
                layer="revocation",
                args={},
                latency_ms=0,
            )
        )
    return session_view(session, state)


@router.get("/sessions/{sid}/audit")
def session_audit(
    sid: str, request: Request, after: str | None = None, identity: UserIdentity = Depends(require_user)
) -> dict:
    state = request.app.state
    _owned_session(sid, identity, state)
    events = state.repo.list_audit(sid, after=after)
    return {"sid": sid, "events": events, "cursor": events[-1]["ts"] if events else after}


@router.get("/mandates/{mandate_id}")
def get_mandate(mandate_id: str, request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    return mandate_view(_owned_mandate(mandate_id, identity, request.app.state))


class MandateBody(BaseModel):
    max: float = Field(gt=0, le=100000)
    merchant: str | None = None
    ship_to_id: str | None = None


@router.post("/mandates", status_code=201)
def create_mandate(body: MandateBody, request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    settings = state.settings
    mandate = new_mandate(
        f"mdt_{secrets.token_hex(4)}",
        identity,
        max_cents=cents_from_amount(body.max),
        merchant=body.merchant or settings.demo_merchant,
        ship_to_id=body.ship_to_id or settings.demo_ship_to_id,
        tool_profile=settings.tool_profile,
    )
    state.repo.put_mandate(mandate)
    return mandate_view(mandate)


@router.post("/demo/reset")
async def demo_reset(request: Request, identity: UserIdentity = Depends(require_user)) -> dict:
    state = request.app.state
    state.repo.reset()
    state.risk.reset()
    mandates = ensure_demo_mandate(state, identity)
    store_reset = True
    try:
        await state.store.call_tool("reset_store", {})
    except Exception:
        store_reset = False
    return {"ok": True, "store_reset": store_reset, "mandates": [mandate_view(m) for m in mandates]}
