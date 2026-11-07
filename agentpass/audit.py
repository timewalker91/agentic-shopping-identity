"""One audit event per decision. The dashboard reads these (contract: section 5, Audit event)."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime

from .aws import table
from .config import T_AUDIT


def record(sid: str, *, agent_id: str, user: str, tool: str, decision: str, reason: str,
           layer: str, args: dict | None = None, latency_ms: int = 0) -> dict:
    ts = datetime.now(UTC).isoformat(timespec="milliseconds")
    item = {
        "sid": sid,
        "sk": f"{ts}#{secrets.token_hex(4)}",
        "ts": ts,
        "agent_id": agent_id,
        "user": user,
        "tool": tool,
        "decision": decision,  # allow | deny | revoked
        "reason": reason,
        "layer": layer,  # token | revocation | policy | budget | risk
        "latency_ms": int(latency_ms),
    }
    if args is not None:
        item["args_digest"] = "sha256:" + hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    table(T_AUDIT).put_item(Item=item)
    return item
