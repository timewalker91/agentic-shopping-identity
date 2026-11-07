"""Revocation list in DynamoDB. Every token exchange and every tool call checks it."""

from __future__ import annotations

import time

from .aws import ddb, table
from .config import T_REVOCATIONS


class RevocationLookupError(RuntimeError):
    """The list could not be read completely. Callers must deny (fail closed)."""


def revoked(keys: list[str]) -> list[str]:
    """Return which of the given keys (sid#..., agent#..., user#..., mandate#..., jti#...) are revoked."""
    resp = ddb().batch_get_item(
        RequestItems={
            T_REVOCATIONS: {
                "Keys": [{"key": {"S": k}} for k in keys],
                "ConsistentRead": True,  # a revocation must be visible on the very next call
                "ProjectionExpression": "#k",
                "ExpressionAttributeNames": {"#k": "key"},
            }
        }
    )
    if resp.get("UnprocessedKeys"):
        raise RevocationLookupError("revocation lookup incomplete")
    return [item["key"]["S"] for item in resp["Responses"].get(T_REVOCATIONS, [])]


def revoke(key: str, *, level: str, reason: str, actor: str) -> None:
    table(T_REVOCATIONS).put_item(
        Item={"key": key, "level": level, "reason": reason, "actor": actor, "created_at": int(time.time())}
    )
