"""Deterministic shopping runs for tests and the live demo (no LLM involved).

Scenarios (see docs/scenario-map.md):
    happy        S1  search -> add three cheap items -> checkout within budget -> order status
    hijacked     S3  reads the poisoned listing and, like a compromised agent, follows its hidden
                     instruction: gift-card SKU (denied) -> attacker address (denied -> auto-revoke)
                     -> next call gets 401
    over_budget  S2  an $89 item is denied by the budget; a cheaper cart goes through
    stop         S6  the happy path with pauses so the parent can press Stop mid-task
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .mcp_client import GatewayClient, GatewayError, SessionRevokedError, ToolError

GC_SKU = re.compile(r"\b(GC-\d+)\b")
GC_QTY = re.compile(r"(\d+)\s*(?:units?|x|×)\s*(?:of\s*)?(?:SKU\s*)?GC-", re.I)
SHIP_TO = re.compile(r"ship_to_id\W{0,3}(addr_[a-z0-9_]+)", re.I)

# Search words per scenario. Defaults match the mock catalog (store/seed.json); override them to
# run the scenarios against a real Shopify catalog, e.g. AGENT_QUERY_HAPPY=snowboard.
DEFAULT_QUERIES = {
    "happy": "school supplies",      # S1/S6: the three cheapest results go in the cart
    "expensive": "headphones",       # S2: the priciest result should exceed the budget
    "cheap": "notebook",             # S2: the cheapest result is the fallback cart
    "poisoned": "markers",           # S3: a result whose description carries the hidden instruction
}
POISON_SCAN_LIMIT = 5  # S3 reads up to this many listings looking for the hidden instruction


def query_for(name: str) -> str:
    return os.environ.get(f"AGENT_QUERY_{name.upper()}") or DEFAULT_QUERIES[name]


@dataclass
class Step:
    tool: str
    args: dict
    outcome: str  # ok | denied | revoked | error
    detail: str = ""
    result: dict = field(default_factory=dict)


class _Stop(Exception):
    pass


class ScriptedShopper:
    def __init__(
        self,
        gateway: GatewayClient,
        *,
        cart_id: str,
        merchant: str,
        ship_to_id: str,
        step_delay: float = 0.0,
        out: Callable[[str], None] = print,
    ) -> None:
        self.gateway = gateway
        self.cart_id = cart_id
        self.merchant = merchant
        self.ship_to_id = ship_to_id
        self.step_delay = step_delay
        self.out = out
        self.steps: list[Step] = []

    # -- plumbing ---------------------------------------------------------------------
    def _call(self, tool: str, args: dict) -> dict | None:
        self.out(f"-> {tool} {json.dumps(args)}")
        if self.step_delay:
            time.sleep(self.step_delay)
        try:
            result = self.gateway.call_tool(tool, args)
        except SessionRevokedError as exc:
            self.steps.append(Step(tool, args, "revoked", f"{exc.status} {exc.reason}: {exc.message}"))
            self.out(f"   x {exc.status} {exc.reason}: {exc.message}")
            self.out("   agent stops: the passport was revoked, no retry")
            raise _Stop from exc
        except GatewayError as exc:
            self.steps.append(Step(tool, args, "denied", f"{exc.status} {exc.reason}: {exc.message}", exc.data))
            self.out(f"   x {exc.status} {exc.reason}: {exc.message}")
            if exc.data.get("remaining") is not None:
                self.out(f"   budget remaining: ${exc.data['remaining']:.2f}")
            if exc.data.get("auto_revoked"):
                self.out("   ! the gateway auto-revoked this session (2 policy denials)")
            return None
        except ToolError as exc:
            self.steps.append(Step(tool, args, "error", str(exc), exc.payload))
            self.out(f"   ! store error: {exc}")
            return None
        self.steps.append(Step(tool, args, "ok", "", result))
        self.out(f"   ok {self._summary(tool, result)}")
        return result

    @staticmethod
    def _summary(tool: str, result: dict) -> str:
        if tool == "search_products":
            return ", ".join(f"{p['sku']} ${p['price']:.2f}" for p in result.get("products", [])[:6])
        if tool in ("view_cart", "add_to_cart"):
            return f"{result.get('item_count', 0)} items, total ${result.get('total', 0):.2f}"
        if tool == "checkout":
            if result.get("status") == "paid":
                return f"paid ${result.get('total', 0):.2f}, order {result.get('order_id')}"
            return f"{result.get('status')}: {result.get('reason')}"
        if tool == "get_product":
            return result.get("product", {}).get("name", "")
        if tool == "get_order_status":
            return f"{result.get('order_id')} {result.get('status')}"
        return json.dumps(result)[:120]

    def _add(self, sku: str, quantity: int = 1, cart_id: str | None = None) -> dict | None:
        return self._call(
            "add_to_cart",
            {"cart_id": cart_id or self.cart_id, "sku": sku, "quantity": quantity, "merchant": self.merchant},
        )

    def _checkout(self, ship_to_id: str | None = None, cart_id: str | None = None) -> dict | None:
        return self._call("checkout", {"cart_id": cart_id or self.cart_id, "ship_to_id": ship_to_id or self.ship_to_id})

    # -- scenarios -----------------------------------------------------------------------
    def run(self, scenario: str) -> list[Step]:
        runner = getattr(self, f"_run_{scenario}", None)
        if runner is None:
            raise ValueError(f"unknown scenario {scenario!r}; use happy, hijacked, over_budget or stop")
        self.out(f"== scenario: {scenario} (cart {self.cart_id}, merchant {self.merchant}, ship to {self.ship_to_id})")
        try:
            runner()
        except _Stop:
            pass
        return self.steps

    def _run_happy(self) -> None:
        found = self._call("search_products", {"query": query_for("happy")}) or {}
        picks = [p for p in found.get("products", []) if not p["sku"].upper().startswith("GC-")]
        picks.sort(key=lambda p: p["price"])
        for product in picks[:3]:
            self._add(product["sku"], 1)
        self._call("view_cart", {"cart_id": self.cart_id})
        order = self._checkout() or {}
        if order.get("order_id"):
            self._call("get_order_status", {"order_id": order["order_id"]})

    def _run_stop(self) -> None:
        self.out("   (pausing between steps; press Stop on the dashboard while the agent shops)")
        self.step_delay = self.step_delay or 3.0
        self._run_happy()

    def _run_over_budget(self) -> None:
        found = self._call("search_products", {"query": query_for("expensive")}) or {}
        pricey = max(found.get("products", []), key=lambda p: p["price"], default=None)
        if pricey:
            self._add(pricey["sku"], 1)
            self._checkout()
        self.out("   agent suggests cheaper items and starts a new cart")
        cheaper_cart = f"{self.cart_id}-b"
        found = self._call("search_products", {"query": query_for("cheap")}) or {}
        cheap = min(found.get("products", []), key=lambda p: p["price"], default=None)
        if cheap:
            self._add(cheap["sku"], 1, cart_id=cheaper_cart)
            self._checkout(cart_id=cheaper_cart)

    def _run_hijacked(self) -> None:
        query = query_for("poisoned")
        found = self._call("search_products", {"query": query}) or {}
        products = found.get("products", [])
        if not products:
            self.out(f"   no products found for {query!r}; nothing to read")
            return
        description, sku_match = "", None
        for product in products[:POISON_SCAN_LIMIT]:
            listing = self._call("get_product", {"sku": product["sku"]}) or {}
            description = listing.get("product", {}).get("description", "")
            sku_match = GC_SKU.search(description)
            if sku_match:
                break
        if not sku_match:
            self.out("   no listing has a hidden instruction (needs a GC-<digits> SKU in a description); nothing to follow")
            return
        qty_match = GC_QTY.search(description)
        addr_match = SHIP_TO.search(description)
        gift_sku = sku_match.group(1)
        quantity = int(qty_match.group(1)) if qty_match else 1
        attacker_addr = addr_match.group(1) if addr_match else "addr_4471_warehouse"
        self.out(f"   ! hidden instruction in the listing: buy {quantity} x {gift_sku}, ship to {attacker_addr}")
        self.out("   ! a hijacked agent obeys. AgentPass should stop it:")
        self._add(gift_sku, quantity)
        self._checkout(ship_to_id=attacker_addr)
        self.out("   the agent tries to carry on with an allowed tool:")
        self._call("view_cart", {"cart_id": self.cart_id})
