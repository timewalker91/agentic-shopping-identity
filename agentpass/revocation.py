"""Keys in the ap_revocations table: sid#…, agent#…, mandate#…, user#…, jti#… (plan, section 5).

The gateway checks all five on every call, with no cache, so a revocation takes effect on the
very next request even though the token itself has not expired.
"""
from __future__ import annotations


def sid_key(sid: str) -> str:
    return f"sid#{sid}"


def keys_for_claims(claims: dict) -> list[str]:
    return [
        f"sid#{claims['sid']}",
        f"jti#{claims['jti']}",
        f"agent#{claims['agent_id']}",
        f"mandate#{claims['mandate_id']}",
        f"user#{claims['sub']}",
    ]


def keys_for_exchange(*, sid: str, user: str, agent_id: str, mandate_id: str) -> list[str]:
    return [f"sid#{sid}", f"agent#{agent_id}", f"mandate#{mandate_id}", f"user#{user}"]
