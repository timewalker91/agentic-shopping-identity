"""Store MCP server on :8100 (mcp SDK MCPServer, Streamable HTTP, stateless, JSON responses).

Same tool contract whether the catalog is the mock seed or a Shopify dev store (plan, section 5).
Only the gateway should reach this port: it binds to 127.0.0.1 and enforces nothing itself.
The hidden tools (change_shipping_address, buy_gift_card) exist here on purpose: the gateway is
what keeps them away from the agent.
"""
from __future__ import annotations

import argparse
import logging
import os
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from .address_book import AddressBook
from .backend import CartBook, CatalogBackend, OrderBook, StoreToolError
from .mock_backend import MockCatalog
from .models import Cart, Order, dollars
from .payment import PaymentMock

log = logging.getLogger("store")

# Shopify checkout status -> our order status (handoff orders only).
SHOPIFY_ORDER_STATUS = {"completed": "paid_by_buyer", "canceled": "canceled", "complete_in_progress": "processing"}


def build_server(
    catalog: CatalogBackend,
    carts: CartBook,
    orders: OrderBook,
    payment: PaymentMock,
    address_book: AddressBook | None = None,
) -> MCPServer:
    """address_book set (Shopify backend + STORE_CHECKOUT=shopify): checkout hands off to Shopify."""
    server = MCPServer(
        name="agentpass-store",
        instructions=f"Store MCP server for merchant '{catalog.merchant}'. Prices are in dollars.",
    )

    async def shopify_handoff(cart: Cart, ship_to_id: str) -> dict[str, Any]:
        """Turn the local cart into a Shopify checkout; the buyer pays at continue_url."""
        assert address_book is not None
        buyer, destination = address_book.buyer_and_destination(ship_to_id)
        lines: list[tuple[str, int]] = []
        for line in cart.lines:
            gid = await catalog.variant_id(line.sku)  # type: ignore[attr-defined]
            if gid is None:
                raise StoreToolError("unknown_variant", f"no Shopify variant for sku {line.sku}")
            lines.append((gid, line.quantity))
        session = await catalog.create_checkout(lines, buyer, destination)  # type: ignore[attr-defined]
        if session.continue_url is None or session.status in ("incomplete", "canceled"):
            detail = "; ".join(session.messages) or session.status
            raise StoreToolError("shopify_checkout_failed", f"Shopify could not prepare the checkout: {detail}")
        shopify_total = session.total_cents if session.total_cents is not None else cart.total_cents
        extra = {
            "payment": "shopify_handoff",
            "shopify_checkout_id": session.checkout_id,
            "shopify_status": session.status,
            "shopify_total": dollars(shopify_total),
            "continue_url": session.continue_url,
        }
        order = orders.create(cart, ship_to_id, payment_id="", status="awaiting_buyer", extra=extra)
        carts.clear(cart.cart_id)
        note = "Shopify does not let agents pay. The parent completes payment at continue_url."
        if shopify_total > cart.total_cents:
            note += f" Shopify's total ${dollars(shopify_total):.2f} includes shipping or tax on top of the cart."
        return {**order.to_dict(), "order_status": order.status, "status": "awaiting_buyer", "cart_id": cart.cart_id, "note": note}

    async def refresh_handoff(order: Order) -> None:
        checkout_id = order.extra.get("shopify_checkout_id")
        if not checkout_id or order.status in ("paid_by_buyer", "canceled"):
            return
        try:
            session = await catalog.get_checkout(checkout_id)  # type: ignore[attr-defined]
        except StoreToolError as exc:
            log.warning("get_checkout %s failed: %s", checkout_id, exc.code)
            return
        order.extra["shopify_status"] = session.status
        order.status = SHOPIFY_ORDER_STATUS.get(session.status, order.status)
        if session.shopify_order:
            order.extra["shopify_order"] = {
                k: session.shopify_order.get(k) for k in ("id", "label", "permalink_url") if session.shopify_order.get(k)
            }

    @server.tool()
    async def search_products(query: str, category: str | None = None) -> dict[str, Any]:
        """Search the catalog by keywords. Optional category filter. Returns up to 8 products with sku, name and price."""
        products = await catalog.search_products(query, category)
        return {"merchant": catalog.merchant, "query": query, "products": [p.summary() for p in products]}

    @server.tool()
    async def get_product(sku: str) -> dict[str, Any]:
        """Get one product's full listing (price, category, seller description) by SKU."""
        product = await catalog.get_product(sku)
        if product is None:
            raise StoreToolError("unknown_sku", f"no product with sku {sku}")
        return {"product": product.detail()}

    @server.tool()
    async def view_cart(cart_id: str) -> dict[str, Any]:
        """Show the items and the total of a cart. Unknown cart ids are empty carts."""
        return carts.get(cart_id).to_dict()

    @server.tool()
    async def add_to_cart(cart_id: str, sku: str, quantity: int, merchant: str) -> dict[str, Any]:
        """Add quantity units of a SKU to a cart at this merchant. Returns the updated cart."""
        if quantity < 1:
            raise StoreToolError("invalid_quantity", "quantity must be at least 1")
        if merchant != catalog.merchant:
            raise StoreToolError("wrong_merchant", f"this store is {catalog.merchant}, not {merchant}")
        product = await catalog.get_product(sku)
        if product is None:
            raise StoreToolError("unknown_sku", f"no product with sku {sku}")
        cart = carts.add(cart_id, product, quantity)
        return {**cart.to_dict(), "added": {"sku": product.sku, "name": product.name, "quantity": quantity}}

    @server.tool()
    async def checkout(cart_id: str, ship_to_id: str) -> dict[str, Any]:
        """Pay for the cart and ship it to the saved address ship_to_id. Returns the order or a decline."""
        cart = carts.get(cart_id)
        if not cart.lines:
            raise StoreToolError("empty_cart", f"cart {cart_id} is empty")
        if address_book is not None:
            return await shopify_handoff(cart, ship_to_id)
        result = payment.charge(cart.total_cents, reference=cart_id)
        if result.status != "paid":
            return {
                "status": "declined",
                "reason": result.reason,
                "cart_id": cart_id,
                "total": cart.to_dict()["total"],
                "currency": cart.currency,
            }
        order = orders.create(cart, ship_to_id, result.payment_id or "")
        carts.clear(cart_id)
        return {**order.to_dict(), "order_status": order.status, "status": "paid", "cart_id": cart_id}

    @server.tool()
    async def get_order_status(order_id: str) -> dict[str, Any]:
        """Look up an order by id."""
        order = orders.get(order_id)
        if order is None:
            raise StoreToolError("unknown_order", f"no order {order_id}")
        await refresh_handoff(order)
        return order.to_dict()

    @server.tool()
    async def change_shipping_address(cart_id: str, ship_to_id: str) -> dict[str, Any]:
        """Change where a cart ships. (Hidden from agents by the gateway.)"""
        cart = carts.set_ship_to(cart_id, ship_to_id)
        return {"status": "updated", "cart_id": cart.cart_id, "ship_to_id": cart.ship_to_id}

    @server.tool()
    async def buy_gift_card(amount: float, recipient_email: str) -> dict[str, Any]:
        """Buy a digital gift card. (Hidden from agents by the gateway.)"""
        cents = int((Decimal(str(amount)) * 100).quantize(Decimal("1")))
        result = payment.charge(cents, reference=f"giftcard:{recipient_email}")
        if result.status != "paid":
            return {"status": "declined", "reason": result.reason, "amount": amount}
        return {"status": "issued", "amount": amount, "recipient_email": recipient_email, "code": "GC-DEMO-CODE"}

    @server.tool()
    async def reset_store() -> dict[str, Any]:
        """Demo reset: clear carts and orders, restore payment funds and the catalog."""
        await catalog.reset()
        carts.reset()
        orders.reset()
        payment.reset()
        return {"status": "reset", "merchant": catalog.merchant, "funds": payment.available_cents / 100}

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "merchant": catalog.merchant,
                "backend": catalog.name,
                "checkout": "shopify_handoff" if address_book is not None else "payment_mock",
                "orders": orders.count(),
                "payment_funds": payment.available_cents / 100,
            }
        )

    return server


def make_catalog(backend: str, merchant: str | None = None) -> CatalogBackend:
    if backend == "shopify":
        from .shopify_backend import ShopifyCatalog

        target = os.environ.get("SHOPIFY_MCP_URL") or os.environ.get("SHOPIFY_STORE_DOMAIN")
        if not target:
            raise SystemExit("STORE_BACKEND=shopify needs SHOPIFY_STORE_DOMAIN (or SHOPIFY_MCP_URL)")
        return ShopifyCatalog(
            target,
            merchant=merchant or os.environ.get("STORE_MERCHANT", "freshmart"),
            profile_url=os.environ.get("UCP_AGENT_PROFILE_URL") or None,
            country=os.environ.get("SHOPIFY_COUNTRY", "US"),
        )
    return MockCatalog()


def create_app(
    backend: str = "mock", host: str = "127.0.0.1", funds_dollars: float = 150.0, checkout: str = "mock"
) -> Any:
    catalog = make_catalog(backend)
    carts = CartBook(catalog.merchant)
    orders = OrderBook()
    payment = PaymentMock(int(round(funds_dollars * 100)))
    address_book = None
    if checkout == "shopify":
        if backend != "shopify":
            raise SystemExit("STORE_CHECKOUT=shopify needs STORE_BACKEND=shopify")
        address_book = AddressBook.load()
        log.info("checkout: Shopify handoff, addresses %s from %s", address_book.ids(), address_book.source)
    server = build_server(catalog, carts, orders, payment, address_book)
    return server.streamable_http_app(json_response=True, stateless_http=True, host=host)


def main() -> None:
    import uvicorn

    from agentpass.config import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Aide-commerce store MCP server")
    parser.add_argument("--host", default=os.environ.get("STORE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STORE_PORT", "8100")))
    parser.add_argument("--backend", choices=("mock", "shopify"), default=os.environ.get("STORE_BACKEND", "mock"))
    parser.add_argument("--funds", type=float, default=float(os.environ.get("PAYMENT_MOCK_FUNDS", "150")))
    parser.add_argument(
        "--checkout",
        choices=("mock", "shopify"),
        default=os.environ.get("STORE_CHECKOUT", "mock"),
        help="mock = payment mock; shopify = real Shopify checkout the buyer pays at continue_url",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(args.backend, args.host, args.funds, args.checkout)
    log.info("store backend=%s checkout=%s listening on http://%s:%d/mcp", args.backend, args.checkout, args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
