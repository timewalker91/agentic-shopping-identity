"""Persistence for agentpass: the five ap_* tables from the plan, section 5.

Two implementations share one interface:
- MemoryRepo: dictionaries, for local mode and tests.
- DynamoRepo: the real tables created by infra/setup.py.

Money is stored as integer cents (max_cents, spent_cents, held_cents) so budget math is exact.
Revocation reads are strongly consistent and raise RevocationError on failure so the
gateway can fail closed.
"""
from __future__ import annotations

import threading
import time
from decimal import Decimal
from typing import Any, Protocol

from .clock import now_iso


class RevocationError(RuntimeError):
    """The revocation list could not be read. Callers must deny the request."""


class Repo(Protocol):
    # mandates (ap_mandates)
    def get_mandate(self, mandate_id: str) -> dict | None: ...
    def put_mandate(self, mandate: dict) -> None: ...
    def list_mandates_for_user(self, user: str) -> list[dict]: ...
    def try_hold(self, mandate_id: str, cents: int) -> tuple[bool, int]: ...
    def settle_hold(self, mandate_id: str, cents: int) -> None: ...
    def release_hold(self, mandate_id: str, cents: int) -> None: ...
    # sessions (ap_sessions)
    def get_session(self, sid: str) -> dict | None: ...
    def put_session(self, session: dict) -> None: ...
    def update_session(self, sid: str, **fields: Any) -> dict | None: ...
    def list_sessions_for_user(self, user: str) -> list[dict]: ...
    # revocations (ap_revocations)
    def revoke(self, key: str, reason: str, by: str) -> None: ...
    def find_revoked(self, keys: list[str]) -> dict | None: ...
    def use_once(self, key: str, ttl_seconds: int) -> bool: ...
    # audit (ap_audit)
    def append_audit(self, event: dict) -> None: ...
    def list_audit(self, sid: str, after: str | None = None, limit: int = 500) -> list[dict]: ...
    # demo
    def reset(self) -> None: ...


def _remaining(mandate: dict) -> int:
    return int(mandate["max_cents"]) - int(mandate["spent_cents"]) - int(mandate["held_cents"])


class MemoryRepo:
    """In-memory tables. Good enough for local mode and tests; everything is lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mandates: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._revocations: dict[str, dict] = {}
        self._audit: dict[str, list[dict]] = {}

    def get_mandate(self, mandate_id: str) -> dict | None:
        with self._lock:
            m = self._mandates.get(mandate_id)
            return dict(m) if m else None

    def put_mandate(self, mandate: dict) -> None:
        with self._lock:
            self._mandates[mandate["mandate_id"]] = dict(mandate)

    def list_mandates_for_user(self, user: str) -> list[dict]:
        with self._lock:
            return [dict(m) for m in self._mandates.values() if m.get("user") == user]

    def try_hold(self, mandate_id: str, cents: int) -> tuple[bool, int]:
        with self._lock:
            m = self._mandates.get(mandate_id)
            if m is None:
                return False, 0
            remaining = _remaining(m)
            if m.get("status") != "active" or cents > remaining:
                return False, max(remaining, 0)
            m["held_cents"] += cents
            return True, remaining - cents

    def settle_hold(self, mandate_id: str, cents: int) -> None:
        with self._lock:
            m = self._mandates[mandate_id]
            m["held_cents"] = max(0, m["held_cents"] - cents)
            m["spent_cents"] += cents

    def release_hold(self, mandate_id: str, cents: int) -> None:
        with self._lock:
            m = self._mandates[mandate_id]
            m["held_cents"] = max(0, m["held_cents"] - cents)

    def get_session(self, sid: str) -> dict | None:
        with self._lock:
            s = self._sessions.get(sid)
            return dict(s) if s else None

    def put_session(self, session: dict) -> None:
        with self._lock:
            self._sessions[session["sid"]] = dict(session)

    def update_session(self, sid: str, **fields: Any) -> dict | None:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None:
                return None
            s.update(fields)
            return dict(s)

    def list_sessions_for_user(self, user: str) -> list[dict]:
        with self._lock:
            rows = [dict(s) for s in self._sessions.values() if s.get("user") == user]
        return sorted(rows, key=lambda s: s.get("created_at", ""), reverse=True)

    def revoke(self, key: str, reason: str, by: str) -> None:
        with self._lock:
            self._revocations[key] = {"key": key, "reason": reason, "by": by, "ts": now_iso()}

    def find_revoked(self, keys: list[str]) -> dict | None:
        with self._lock:
            for key in keys:
                hit = self._revocations.get(key)
                if hit:
                    return dict(hit)
        return None

    def use_once(self, key: str, ttl_seconds: int) -> bool:
        """Record a single-use value (an agent assertion's jti). False if it was already used."""
        now = time.time()
        with self._lock:
            self._used = {k: exp for k, exp in getattr(self, "_used", {}).items() if exp > now}
            if key in self._used:
                return False
            self._used[key] = now + ttl_seconds
            return True

    def append_audit(self, event: dict) -> None:
        with self._lock:
            self._audit.setdefault(event["sid"], []).append(dict(event))

    def list_audit(self, sid: str, after: str | None = None, limit: int = 500) -> list[dict]:
        with self._lock:
            events = self._audit.get(sid, [])
            if after:
                events = [e for e in events if e["ts"] > after]
            return [dict(e) for e in events[:limit]]

    def reset(self) -> None:
        with self._lock:
            self._mandates.clear()
            self._sessions.clear()
            self._revocations.clear()
            self._audit.clear()


def _plain(value: Any) -> Any:
    """Turn DynamoDB Decimals back into ints/floats, recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _dynamo(value: Any) -> Any:
    """DynamoDB refuses floats; store them as Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dynamo(v) for v in value]
    return value


class DynamoRepo:
    """The ap_* DynamoDB tables. Table names and keys follow the plan, section 5."""

    def __init__(self, prefix: str, region: str) -> None:
        import boto3  # imported here so local mode never needs AWS

        self._ddb = boto3.resource("dynamodb", region_name=region)
        self._names = {
            "mandates": f"{prefix}mandates",
            "sessions": f"{prefix}sessions",
            "revocations": f"{prefix}revocations",
            "audit": f"{prefix}audit",
        }
        self._mandates = self._ddb.Table(self._names["mandates"])
        self._sessions = self._ddb.Table(self._names["sessions"])
        self._revocations = self._ddb.Table(self._names["revocations"])
        self._audit = self._ddb.Table(self._names["audit"])

    # -- mandates -----------------------------------------------------------------
    def get_mandate(self, mandate_id: str) -> dict | None:
        item = self._mandates.get_item(Key={"mandate_id": mandate_id}, ConsistentRead=True).get("Item")
        return _plain(item) if item else None

    def put_mandate(self, mandate: dict) -> None:
        self._mandates.put_item(Item=_dynamo(mandate))

    def list_mandates_for_user(self, user: str) -> list[dict]:
        return self._scan_for_user(self._mandates, user)

    def try_hold(self, mandate_id: str, cents: int) -> tuple[bool, int]:
        """Place a budget hold. DynamoDB conditions can't do arithmetic, so: read the mandate,
        check the limit in code, then update only if spent/held/max still have the values we
        read (optimistic concurrency). Two concurrent checkouts can never both pass."""
        from botocore.exceptions import ClientError

        for _ in range(3):
            m = self.get_mandate(mandate_id)
            if m is None:
                return False, 0
            remaining = _remaining(m)
            if m.get("status") != "active" or cents > remaining:
                return False, max(remaining, 0)
            try:
                self._mandates.update_item(
                    Key={"mandate_id": mandate_id},
                    UpdateExpression="SET held_cents = :new_held",
                    ConditionExpression="#st = :active AND spent_cents = :s AND held_cents = :h AND max_cents = :m",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":new_held": int(m["held_cents"]) + cents,
                        ":s": int(m["spent_cents"]),
                        ":h": int(m["held_cents"]),
                        ":m": int(m["max_cents"]),
                        ":active": "active",
                    },
                )
                return True, remaining - cents
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                # someone changed the budget since we read it: read again and retry
        current = self.get_mandate(mandate_id)
        return False, max(_remaining(current), 0) if current else 0

    def settle_hold(self, mandate_id: str, cents: int) -> None:
        self._mandates.update_item(
            Key={"mandate_id": mandate_id},
            UpdateExpression="SET held_cents = held_cents - :c, spent_cents = spent_cents + :c",
            ExpressionAttributeValues={":c": cents},
        )

    def release_hold(self, mandate_id: str, cents: int) -> None:
        self._mandates.update_item(
            Key={"mandate_id": mandate_id},
            UpdateExpression="SET held_cents = held_cents - :c",
            ExpressionAttributeValues={":c": cents},
        )

    # -- sessions -----------------------------------------------------------------
    def get_session(self, sid: str) -> dict | None:
        item = self._sessions.get_item(Key={"sid": sid}, ConsistentRead=True).get("Item")
        return _plain(item) if item else None

    def put_session(self, session: dict) -> None:
        self._sessions.put_item(Item=_dynamo(session))

    def update_session(self, sid: str, **fields: Any) -> dict | None:
        if not fields:
            return self.get_session(sid)
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": _dynamo(v) for i, v in enumerate(fields.values())}
        expr = "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(fields)))
        resp = self._sessions.update_item(
            Key={"sid": sid},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(sid)",
            ReturnValues="ALL_NEW",
        )
        return _plain(resp.get("Attributes"))

    def list_sessions_for_user(self, user: str) -> list[dict]:
        rows = self._scan_for_user(self._sessions, user)
        return sorted(rows, key=lambda s: s.get("created_at", ""), reverse=True)

    # -- revocations ---------------------------------------------------------------
    def revoke(self, key: str, reason: str, by: str) -> None:
        self._revocations.put_item(Item={"key": key, "reason": reason, "by": by, "ts": now_iso()})

    def find_revoked(self, keys: list[str]) -> dict | None:
        name = self._names["revocations"]
        try:
            resp = self._ddb.batch_get_item(
                RequestItems={name: {"Keys": [{"key": k} for k in keys], "ConsistentRead": True}}
            )
        except Exception as exc:  # any failure here must deny the call
            raise RevocationError(f"revocation list unavailable: {exc.__class__.__name__}") from exc
        if resp.get("UnprocessedKeys", {}).get(name):
            raise RevocationError("revocation list read was incomplete")
        found = {item["key"]: item for item in resp.get("Responses", {}).get(name, [])}
        for key in keys:
            if key in found:
                return _plain(found[key])
        return None

    def use_once(self, key: str, ttl_seconds: int) -> bool:
        """Conditional put: succeeds only the first time. Stored in ap_revocations with a TTL."""
        try:
            self._revocations.put_item(
                Item={"key": key, "reason": "single-use value", "by": "token-service", "ts": now_iso(),
                      "ttl": int(time.time()) + ttl_seconds},
                ConditionExpression="attribute_not_exists(#k)",
                ExpressionAttributeNames={"#k": "key"},
            )
            return True
        except self._revocations.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    # -- audit ------------------------------------------------------------------------
    def append_audit(self, event: dict) -> None:
        item = {**event, "sk": f"{event['ts']}#{event['id']}"}
        self._audit.put_item(Item=_dynamo(item))

    def list_audit(self, sid: str, after: str | None = None, limit: int = 500) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        cond = Key("sid").eq(sid)
        if after:
            cond = cond & Key("sk").gt(f"{after}#~")
        resp = self._audit.query(KeyConditionExpression=cond, Limit=limit, ScanIndexForward=True)
        return [_plain(item) for item in resp.get("Items", [])]

    # -- demo -------------------------------------------------------------------------
    def reset(self) -> None:
        for table, key_names in (
            (self._mandates, ("mandate_id",)),
            (self._sessions, ("sid",)),
            (self._revocations, ("key",)),
            (self._audit, ("sid", "sk")),
        ):
            items = self._scan_all(table)
            with table.batch_writer() as batch:
                for item in items:
                    batch.delete_item(Key={k: item[k] for k in key_names})

    # -- helpers ----------------------------------------------------------------------
    @staticmethod
    def _scan_all(table: Any, **kwargs: Any) -> list[dict]:
        items: list[dict] = []
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
            items.extend(resp.get("Items", []))
        return items

    def _scan_for_user(self, table: Any, user: str) -> list[dict]:
        from boto3.dynamodb.conditions import Attr

        return [_plain(i) for i in self._scan_all(table, FilterExpression=Attr("user").eq(user))]
