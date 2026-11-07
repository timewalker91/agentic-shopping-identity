"""Catalog, cart and order records. Money is integer cents inside, dollars in tool results."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime


def dollars(cents: int) -> float:
    return round(cents / 100, 2)


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int
    currency: str
    category: str
    merchant: str
    description: str
    tags: tuple[str, ...] = ()
    image_url: str | None = None

    def summary(self) -> dict:
        return {
            "sku": self.sku,
            "name": self.name,
            "price": dollars(self.price_cents),
            "currency": self.currency,
            "category": self.category,
            "merchant": self.merchant,
        }

    def detail(self) -> dict:
        return {**self.summary(), "description": self.description, "image_url": self.image_url}


@dataclass
class CartLine:
    sku: str
    name: str
    unit_price_cents: int
    quantity: int

    @property
    def line_total_cents(self) -> int:
        return self.unit_price_cents * self.quantity

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "name": self.name,
            "quantity": self.quantity,
            "unit_price": dollars(self.unit_price_cents),
            "line_total": dollars(self.line_total_cents),
        }


@dataclass
class Cart:
    cart_id: str
    merchant: str
    currency: str = "USD"
    lines: list[CartLine] = field(default_factory=list)
    ship_to_id: str | None = None

    @property
    def total_cents(self) -> int:
        return sum(line.line_total_cents for line in self.lines)

    def add(self, product: Product, quantity: int) -> None:
        for line in self.lines:
            if line.sku == product.sku:
                line.quantity += quantity
                return
        self.lines.append(CartLine(product.sku, product.name, product.price_cents, quantity))

    def to_dict(self) -> dict:
        return {
            "cart_id": self.cart_id,
            "merchant": self.merchant,
            "items": [line.to_dict() for line in self.lines],
            "item_count": sum(line.quantity for line in self.lines),
            "total": dollars(self.total_cents),
            "currency": self.currency,
            "ship_to_id": self.ship_to_id,
        }


@dataclass
class Order:
    order_id: str
    cart_id: str
    merchant: str
    ship_to_id: str
    total_cents: int
    currency: str
    items: list[dict]
    payment_id: str
    status: str = "confirmed"
    placed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @classmethod
    def from_cart(cls, cart: Cart, ship_to_id: str, payment_id: str) -> "Order":
        return cls(
            order_id=f"ord_{secrets.token_hex(4)}",
            cart_id=cart.cart_id,
            merchant=cart.merchant,
            ship_to_id=ship_to_id,
            total_cents=cart.total_cents,
            currency=cart.currency,
            items=[line.to_dict() for line in cart.lines],
            payment_id=payment_id,
        )

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "status": self.status,
            "merchant": self.merchant,
            "ship_to_id": self.ship_to_id,
            "total": dollars(self.total_cents),
            "currency": self.currency,
            "items": self.items,
            "payment_id": self.payment_id,
            "placed_at": self.placed_at,
        }
