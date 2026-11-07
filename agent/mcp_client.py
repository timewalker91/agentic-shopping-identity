"""MCP client for the gateway: JSON-RPC over HTTP (Streamable HTTP, JSON responses).

Maps the gateway's error contract (plan, section 5) to exceptions the shopper can act on:
    session_revoked -> SessionRevokedError  (stop the task, no retry)
    policy_denied / over_budget / mandate_inactive -> GatewayError (don't retry; explain)
    token_expired   -> refreshed once through the broker, then retried
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .broker import BrokerError, CredentialBroker

PROTOCOL_VERSION = "2025-06-18"


class GatewayError(Exception):
    def __init__(self, status: int, reason: str, message: str, data: dict | None = None, code: int | None = None):
        super().__init__(f"{status} {reason}: {message}")
        self.status = status
        self.reason = reason
        self.message = message
        self.data = data or {}
        self.code = code


class SessionRevokedError(GatewayError):
    pass


class ToolError(Exception):
    """The store rejected the call (unknown sku, empty cart...). The gateway allowed it."""

    def __init__(self, message: str, payload: dict | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


class GatewayClient:
    def __init__(self, broker: CredentialBroker, timeout: float = 60.0) -> None:
        self.broker = broker
        self._http = httpx.Client(base_url=broker.api_url, timeout=timeout)
        self._next_id = 1

    def _headers(self) -> dict[str, str]:
        try:
            auth = self.broker.auth_headers()
        except BrokerError as exc:
            if exc.reason in ("session_revoked", "unknown_session", "mandate_inactive"):
                raise SessionRevokedError(exc.status or 401, exc.reason, exc.message) from exc
            raise GatewayError(exc.status or 0, exc.reason, exc.message) from exc
        return {**auth, "Accept": "application/json", "Content-Type": "application/json", "MCP-Protocol-Version": PROTOCOL_VERSION}

    def _rpc(self, method: str, params: dict | None = None, *, retry_on_expiry: bool = True) -> dict:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._next_id += 1
        resp = self._http.post("/mcp", json=payload, headers=self._headers())
        try:
            body = resp.json()
        except ValueError:
            raise GatewayError(resp.status_code, "bad_response", resp.text[:200]) from None
        if "error" in body:
            err = body["error"]
            data = err.get("data") or {}
            reason = data.get("reason", "error")
            if reason == "token_expired" and retry_on_expiry:
                return self._rpc(method, params, retry_on_expiry=False)
            cls = SessionRevokedError if reason == "session_revoked" else GatewayError
            raise cls(resp.status_code, reason, err.get("message", ""), data, err.get("code"))
        return body.get("result") or {}

    def notify(self, method: str) -> None:
        self._http.post("/mcp", json={"jsonrpc": "2.0", "method": method}, headers=self._headers())

    def initialize(self) -> dict:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agentpass-shopper", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        payload = result.get("structuredContent")
        text = next((b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"), "")
        if payload is None:
            try:
                payload = json.loads(text) if text else {}
            except json.JSONDecodeError:
                payload = {"text": text}
        if result.get("isError"):
            raise ToolError(payload.get("message") or text or "tool failed", payload)
        return payload
