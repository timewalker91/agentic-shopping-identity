"""Audit events, one per tool call (plan, section 5). The dashboard polls these.

Arguments are never stored, only a SHA-256 digest, so payment data and addresses never end up
in the log.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from .clock import now_iso

DECISIONS = ("allow", "deny", "revoked")
LAYERS = ("token", "revocation", "policy", "budget", "risk", "gateway")


def args_digest(args: Any) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def new_event(
    *,
    sid: str,
    agent_id: str,
    user: str,
    tool: str,
    decision: str,
    reason: str,
    layer: str,
    args: Any,
    latency_ms: float,
) -> dict:
    assert decision in DECISIONS, decision
    assert layer in LAYERS, layer
    return {
        "ts": now_iso(),
        "id": secrets.token_hex(4),
        "sid": sid,
        "agent_id": agent_id,
        "user": user,
        "tool": tool,
        "decision": decision,
        "reason": reason,
        "layer": layer,
        "args_digest": args_digest(args),
        "latency_ms": round(latency_ms, 1),
    }
