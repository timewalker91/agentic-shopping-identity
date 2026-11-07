"""Money helpers. Budgets live on the mandate as integer cents; the API speaks dollars."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def cents_from_amount(amount: Any) -> int:
    """32.47 -> 3247. Accepts float, int, str or Decimal; rounds half up to the cent."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dollars(cents: int) -> float:
    return round(cents / 100, 2)


def budget_view(mandate: dict) -> dict:
    """The budget fields the Control API returns: max, spent, held, remaining (dollars)."""
    max_c = int(mandate["max_cents"])
    spent_c = int(mandate["spent_cents"])
    held_c = int(mandate["held_cents"])
    return {
        "max": dollars(max_c),
        "spent": dollars(spent_c),
        "held": dollars(held_c),
        "remaining": dollars(max(max_c - spent_c - held_c, 0)),
        "currency": mandate.get("currency", "USD"),
    }


def mandate_view(mandate: dict) -> dict:
    return {
        "mandate_id": mandate["mandate_id"],
        "user": mandate["user"],
        "username": mandate.get("username"),
        "merchant": mandate["merchant"],
        "ship_to_id": mandate["ship_to_id"],
        "tool_profile": mandate["tool_profile"],
        "status": mandate["status"],
        "created_at": mandate.get("created_at"),
        **budget_view(mandate),
    }
