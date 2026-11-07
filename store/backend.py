"""Pluggable catalog (mock seed or Shopify) plus the local cart and order books.

Carts live in this process for both backends: checkout always goes to our payment mock, so a
Shopify-side cart would add nothing to the demo and would couple us to Shopify's changing cart
tools.
"""
from __future__ import annotations

import threading
from typing import Protocol

from mcp.server.mcpserver.exceptions import ToolError

from .models import Cart, Order, Product


class StoreToolError(ToolError):
    """A tool-level failure the agent can reason about: `code: message`.

    Subclassing the SDK's ToolError keeps the text in the MCP error result; any other exception
    is hidden from the client as "Error executing tool".
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class CatalogBackend(Protocol):
    merchant: str
    name: str

    async def search_products(self, query: str, category: str | None = None, limit: int = 8) -> list[Product]: ...
    async def get_product(self, sku: str) -> Product | None: ...
    async def reset(self) -> None: ...


class CartBook:
    def __init__(self, merchant: str) -> None:
        self._merchant = merchant
        self._lock = threading.Lock()
        self._carts: dict[str, Cart] = {}

    def get(self, cart_id: str) -> Cart:
        with self._lock:
            cart = self._carts.get(cart_id)
            if cart is None:
                cart = self._carts[cart_id] = Cart(cart_id=cart_id, merchant=self._merchant)
            return cart

    def add(self, cart_id: str, product: Product, quantity: int) -> Cart:
        cart = self.get(cart_id)
        with self._lock:
            cart.add(product, quantity)
        return cart

    def set_ship_to(self, cart_id: str, ship_to_id: str) -> Cart:
        cart = self.get(cart_id)
        with self._lock:
            cart.ship_to_id = ship_to_id
        return cart

    def clear(self, cart_id: str) -> None:
        with self._lock:
            self._carts.pop(cart_id, None)

    def reset(self) -> None:
        with self._lock:
            self._carts.clear()


class OrderBook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._orders: dict[str, Order] = {}

    def create(self, cart: Cart, ship_to_id: str, payment_id: str) -> Order:
        order = Order.from_cart(cart, ship_to_id, payment_id)
        with self._lock:
            self._orders[order.order_id] = order
        return order

    def get(self, order_id: str) -> Order | None:
        with self._lock:
            return self._orders.get(order_id)

    def count(self) -> int:
        with self._lock:
            return len(self._orders)

    def reset(self) -> None:
        with self._lock:
            self._orders.clear()
