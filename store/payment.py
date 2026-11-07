"""Payment mock. No card data exists anywhere: it only answers paid or declined.

It starts with a fixed amount of funds (PAYMENT_MOCK_FUNDS dollars) and declines with
`insufficient_funds` once they run out, so the team can show a store-side failure that is
separate from the AgentPass budget check.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class PaymentResult:
    status: str  # "paid" | "declined"
    payment_id: str | None = None
    reason: str | None = None


class PaymentMock:
    def __init__(self, funds_cents: int) -> None:
        self._initial = funds_cents
        self._funds = funds_cents
        self._lock = threading.Lock()

    @property
    def available_cents(self) -> int:
        with self._lock:
            return self._funds

    def charge(self, cents: int, reference: str) -> PaymentResult:
        if cents <= 0:
            return PaymentResult("declined", reason="empty_cart")
        with self._lock:
            if cents > self._funds:
                return PaymentResult("declined", reason="insufficient_funds")
            self._funds -= cents
        return PaymentResult("paid", payment_id=f"pay_{secrets.token_hex(4)}")

    def reset(self) -> None:
        with self._lock:
            self._funds = self._initial
