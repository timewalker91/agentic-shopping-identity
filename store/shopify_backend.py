"""Shopify store adapter: catalog through the store's UCP MCP endpoint, carts kept local.

Shopify storefronts expose the Universal Commerce Protocol (UCP) over MCP at
https://<shop>/api/ucp/mcp with the tools search_catalog, lookup_catalog and get_product.
Every call carries the URL of a UCP *agent profile* in meta.ucp-agent.profile; Shopify fetches
that profile to negotiate capabilities ("UCP discovery"), so it must be a public HTTPS URL.
By default we use the profile fixture Shopify hosts for testing; set UCP_AGENT_PROFILE_URL to a
profile you host yourself (docs/ucp-agent-profile.json is the template).

Arguments are nested ({"meta": {...}, "catalog": {...}}), results come back in structuredContent,
and prices are integers in minor units ({"amount": 1899, "currency": "USD"} is $18.99).
Stores that still serve the older Storefront MCP tools (search_shop_catalog on /api/mcp) are
handled by the legacy path.

The tool contract is the mock's: `sku` is the variant SKU set in Shopify admin (give the gift
card SKU GC-100 so the same Cedar rule trips); variants without a SKU get SHOP-<variant id>.
Run `python -m store.shopify_probe <shop-domain> [query]` to see what a store answers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from .backend import StoreToolError
from .models import Product

DEFAULT_PROFILE_URL = "https://shopify.dev/ucp/agent-profiles/2026-08-25/valid-with-capabilities.json"
UCP_SEARCH, UCP_LOOKUP, UCP_DETAIL = "search_catalog", "lookup_catalog", "get_product"
LEGACY_SEARCH = ("search_shop_catalog",)
LEGACY_DETAIL = ("get_product_details",)
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def candidate_endpoints(domain_or_url: str) -> list[str]:
    if domain_or_url.startswith("http"):
        return [domain_or_url.rstrip("/")]
    domain = domain_or_url.strip().rstrip("/")
    return [f"https://{domain}/api/ucp/mcp", f"https://{domain}/api/mcp"]


def _parse_sse(text: str) -> dict:
    """Streamable HTTP may answer with text/event-stream; the result is in the last `data:` JSON."""
    last: dict = {}
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                last = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
    return last


def _rpc_error(body: dict) -> StoreToolError:
    err = body.get("error") or {}
    data = err.get("data") if isinstance(err.get("data"), dict) else {}
    code = str(data.get("code") or err.get("code") or "rpc_error")
    detail = data.get("content") or data.get("message") or ""
    message = f"{err.get('message', 'error')}: {detail}".rstrip(": ")
    if code == "profile_unreachable":
        message += " (Shopify could not fetch the UCP agent profile URL; set UCP_AGENT_PROFILE_URL to a public HTTPS JSON)"
    return StoreToolError(f"shopify_{code}", message)


async def mcp_rpc(client: httpx.AsyncClient, url: str, method: str, params: dict | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    resp = await client.post(url, json=payload, headers=HEADERS)
    body: dict = {}
    if "text/event-stream" in resp.headers.get("content-type", ""):
        body = _parse_sse(resp.text)
    else:
        try:
            body = resp.json()
        except ValueError:
            body = {}
    if isinstance(body, dict) and "error" in body:
        raise _rpc_error(body)
    if resp.status_code >= 400:
        raise StoreToolError("shopify_http_error", f"{method} -> HTTP {resp.status_code} from {url}")
    return body.get("result", {}) if isinstance(body, dict) else {}


def pick(obj: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(obj, dict):
        return default
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return default


def tool_payload(result: dict) -> Any:
    """UCP puts the JSON in structuredContent; older servers return it as text content."""
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except json.JSONDecodeError:
                return {"text": block["text"]}
    return {}


def _strip_html(text: str) -> str:
    text = re.sub(r"<!--(.*?)-->", r" \1 ", text, flags=re.S)  # keep comment text: hidden instructions live there
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _category_name(categories: list) -> str:
    """Prefer a readable taxonomy name; Shopify's own taxonomy values are gids like .../aa-8."""
    values = [str(c.get("value")) for c in categories if isinstance(c, dict) and c.get("value")]
    for value in values:
        if not value.startswith("gid://"):
            return value.split(">")[-1].strip().lower()
    return values[0].rsplit("/", 1)[-1] if values else "general"


def _minor_units(value: Any) -> int | None:
    """{"amount": 1899, "currency": "USD"} -> 1899; a legacy "18.99" string -> 1899."""
    if isinstance(value, dict):
        value = pick(value, "amount", "min")
        if isinstance(value, dict):
            value = value.get("amount")
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        return None


class ShopifyCatalog:
    name = "shopify"

    def __init__(
        self,
        domain_or_url: str,
        merchant: str = "freshmart",
        profile_url: str | None = None,
        country: str = "US",
        timeout: float = 20.0,
    ) -> None:
        self.merchant = merchant
        self._endpoints = candidate_endpoints(domain_or_url)
        self._profile_url = profile_url or DEFAULT_PROFILE_URL
        self._country = country
        self._http = httpx.AsyncClient(timeout=timeout)
        self._url: str | None = None
        self._mode = ""  # "ucp" | "legacy"
        self._search_tool = ""
        self._detail_tool = ""
        self._lookup_tool = ""
        self._index: dict[str, Product] = {}
        self._gids: dict[str, str] = {}
        self._variants: dict[str, str] = {}  # sku -> gid://shopify/ProductVariant/..., for checkout

    @property
    def endpoint(self) -> str:
        return self._url or self._endpoints[0]

    # -- transport ------------------------------------------------------------------------
    async def _ensure_ready(self) -> None:
        if self._url:
            return
        tried: list[str] = []
        for url in self._endpoints:
            try:
                tools = (await mcp_rpc(self._http, url, "tools/list")).get("tools", [])
            except (StoreToolError, httpx.HTTPError) as exc:
                tried.append(f"{url}: {exc}")
                continue
            names = {t.get("name") for t in tools}
            if UCP_SEARCH in names:
                self._url, self._mode, self._search_tool = url, "ucp", UCP_SEARCH
                self._detail_tool = UCP_DETAIL if UCP_DETAIL in names else ""
                self._lookup_tool = UCP_LOOKUP if UCP_LOOKUP in names else ""
                return
            legacy = next((n for n in LEGACY_SEARCH if n in names), None)
            if legacy:
                self._url, self._mode, self._search_tool = url, "legacy", legacy
                self._detail_tool = next((n for n in LEGACY_DETAIL if n in names), "")
                return
            tried.append(f"{url}: tools {sorted(n for n in names if n)} have no catalog search")
        raise StoreToolError("shopify_unavailable", "no catalog tool found; " + " | ".join(tried))

    async def _call(self, tool: str, args: dict) -> Any:
        if self._mode == "ucp":
            return await self._ucp(tool, {"catalog": args})
        return await self._raw_call(tool, args)

    async def _ucp(self, tool: str, arguments: dict) -> Any:
        """UCP tools take their payload next to a `meta` block naming our agent profile."""
        return await self._raw_call(tool, {"meta": {"ucp-agent": {"profile": self._profile_url}}, **arguments})

    async def _raw_call(self, tool: str, arguments: dict) -> Any:
        result = await mcp_rpc(self._http, self._url or "", "tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise StoreToolError("shopify_tool_error", json.dumps(tool_payload(result))[:300])
        return tool_payload(result)

    # -- mapping ---------------------------------------------------------------------------
    def _remember(self, product: Product, product_gid: Any, variant_gid: Any = None) -> Product:
        key = product.sku.upper()
        self._index[key] = product
        if product_gid:
            self._gids[key] = str(product_gid)
        if variant_gid and str(variant_gid).startswith("gid://shopify/ProductVariant/"):
            self._variants[key] = str(variant_gid)
        return product

    @staticmethod
    def _finish_sku(sku: str, title: str, fallback: str) -> str:
        sku = (sku or "").strip() or f"SHOP-{fallback}"
        if "gift card" in title.lower() and not sku.upper().startswith("GC-"):
            sku = f"GC-{sku}"
        return sku

    def _ucp_product(self, raw: dict) -> Product:
        variants = [v for v in raw.get("variants") or [] if isinstance(v, dict)]
        first = next((v for v in variants if (v.get("availability") or {}).get("available", True)), variants[0] if variants else {})
        title = str(raw.get("title") or "Untitled")
        price = first.get("price") or (raw.get("price_range") or {}).get("min") or {}
        variant_id = str(first.get("id") or "").rsplit("/", 1)[-1]
        description = raw.get("description")
        if isinstance(description, dict):
            text = description.get("plain") or _strip_html(str(description.get("html") or ""))
        else:
            text = _strip_html(str(description or ""))
        category = _category_name(raw.get("categories") or [])
        media = raw.get("media") or []
        product = Product(
            sku=self._finish_sku(str(first.get("sku") or ""), title, variant_id or str(raw.get("handle") or "item")),
            name=title,
            price_cents=_minor_units(price) or 0,
            currency=str(pick(price, "currency", default="USD")),
            category=category,
            merchant=self.merchant,
            description=text,
            image_url=media[0].get("url") if media and isinstance(media[0], dict) else None,
        )
        return self._remember(product, raw.get("id"), first.get("id"))

    def _legacy_product(self, raw: dict) -> Product:
        variants = raw.get("variants") or []
        first = variants[0] if variants and isinstance(variants[0], dict) else {}
        title = str(pick(raw, "title", "name", default="Untitled"))
        price = _minor_units(pick(first, "price")) or _minor_units(pick(raw, "price_range", "price")) or 0
        variant_id = str(pick(first, "variant_id", "id", default="")).rsplit("/", 1)[-1]
        description = str(pick(raw, "description", "description_html", default=""))
        product = Product(
            sku=self._finish_sku(str(pick(first, "sku", default="")), title, variant_id or "item"),
            name=title,
            price_cents=price,
            currency=str(pick(first, "currency", default=pick(raw, "currency", default="USD"))),
            category=str(pick(raw, "product_type", "category", default="general")),
            merchant=self.merchant,
            description=_strip_html(description),
            image_url=pick(raw, "image_url"),
        )
        return self._remember(product, pick(raw, "product_id", "id"))

    def _to_product(self, raw: dict) -> Product:
        return self._ucp_product(raw) if self._mode == "ucp" else self._legacy_product(raw)

    # -- CatalogBackend ----------------------------------------------------------------------
    async def search_products(self, query: str, category: str | None = None, limit: int = 8) -> list[Product]:
        await self._ensure_ready()
        text = f"{query} {category}".strip() if category else query
        if self._mode == "ucp":
            args: dict[str, Any] = {
                "query": text,
                "context": {"address_country": self._country, "intent": text},
                "pagination": {"limit": limit},
            }
        else:
            args = {"query": text, "context": f"A shopper is looking for: {text}"}
        payload = await self._call(self._search_tool, args)
        items = payload if isinstance(payload, list) else pick(payload, "products", "items", "results", default=[])
        products = [self._to_product(p) for p in items if isinstance(p, dict)]
        if category:
            narrowed = [p for p in products if category.lower() in p.category.lower()]
            products = narrowed or products
        return products[:limit]

    async def get_product(self, sku: str) -> Product | None:
        await self._ensure_ready()
        key = sku.upper()
        if key not in self._index:
            await self._resolve(sku)
        product = self._index.get(key)
        gid = self._gids.get(key)
        if product and gid and self._detail_tool:
            try:
                args = {"id": gid} if self._mode == "ucp" else {"product_id": gid}
                payload = await self._call(self._detail_tool, args)
                raw = pick(payload, "product", default=payload)
                if isinstance(raw, dict) and raw.get("title"):
                    product = self._to_product(raw)
            except StoreToolError:
                pass  # the search result already carries what we need
        return product

    async def _resolve(self, sku: str) -> None:
        """A sku we have not seen in this process: look it up by variant id, else search for it."""
        key = sku.upper()
        tail = key.removeprefix("GC-").removeprefix("SHOP-")
        if self._mode == "ucp" and self._lookup_tool and tail.isdigit():
            try:
                payload = await self._call(self._lookup_tool, {"ids": [f"gid://shopify/ProductVariant/{tail}"]})
                for raw in pick(payload, "products", default=[]):
                    if isinstance(raw, dict):
                        self._to_product(raw)
                if key in self._index:
                    return
            except StoreToolError:
                pass
        await self.search_products(sku, limit=10)

    # -- checkout (Shopify Checkout MCP, general access) ------------------------------------
    # Shopify does not let self-serve agents pay: complete_checkout answers
    # checkout_completion_ineligible unless Shopify has granted the client that permission.
    # The supported path is a handoff: we build the checkout (items, contact, address) and the
    # buyer pays on the merchant's own checkout page at continue_url.
    async def variant_id(self, sku: str) -> str | None:
        await self._ensure_ready()
        key = sku.upper()
        if key not in self._variants:
            await self._resolve(sku)
        return self._variants.get(key)

    async def create_checkout(self, lines: list[tuple[str, int]], buyer: dict, destination: dict) -> CheckoutSession:
        """lines are (variant gid, quantity); buyer has email and/or phone_number."""
        await self._ensure_ready()
        if self._mode != "ucp":
            raise StoreToolError("shopify_checkout_unavailable", "this store has no UCP checkout endpoint")
        checkout = {
            "line_items": [{"quantity": qty, "item": {"id": gid}} for gid, qty in lines],
            "buyer": {k: v for k, v in buyer.items() if v},
            "fulfillment": {"methods": [{"type": "shipping", "destinations": [destination]}]},
            "context": {"address_country": destination.get("address_country") or self._country},
        }
        return CheckoutSession.parse(await self._ucp("create_checkout", {"checkout": checkout}))

    async def get_checkout(self, checkout_id: str) -> CheckoutSession:
        await self._ensure_ready()
        return CheckoutSession.parse(await self._ucp("get_checkout", {"id": checkout_id}))

    async def reset(self) -> None:
        self._index.clear()
        self._gids.clear()
        self._variants.clear()


@dataclass
class CheckoutSession:
    """The parts of a UCP checkout the store reports back to the agent."""

    checkout_id: str
    status: str  # incomplete | requires_escalation | ready_for_complete | completed | canceled ...
    continue_url: str | None
    total_cents: int | None
    currency: str
    messages: list[str]
    shopify_order: dict | None = None

    @classmethod
    def parse(cls, payload: Any) -> "CheckoutSession":
        co = pick(payload, "checkout", default=payload) if isinstance(payload, dict) else {}
        if not isinstance(co, dict) or not co.get("id"):
            raise StoreToolError("shopify_checkout_failed", f"unexpected checkout response: {json.dumps(payload)[:200]}")
        totals = {t.get("type"): t.get("amount") for t in co.get("totals") or [] if isinstance(t, dict)}
        url = co.get("continue_url")
        # Only ever pass on an HTTPS handoff link (Shopify's guidance for continue_url).
        if not (isinstance(url, str) and urlsplit(url).scheme == "https"):
            url = None
        messages = [
            f"{m.get('severity', m.get('type', 'info'))}: {m.get('code', '')} {m.get('content', '')}".strip()
            for m in co.get("messages") or []
            if isinstance(m, dict)
        ]
        order = co.get("order") if isinstance(co.get("order"), dict) else None
        return cls(
            checkout_id=str(co["id"]),
            status=str(co.get("status") or "unknown"),
            continue_url=url,
            total_cents=_minor_units(totals.get("total")),
            currency=str(co.get("currency") or "USD"),
            messages=messages,
            shopify_order=order,
        )
