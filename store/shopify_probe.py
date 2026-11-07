"""Spike helper: show what a Shopify store's UCP / Storefront MCP endpoint actually offers.

    python -m store.shopify_probe <store-domain or url> [search query]

Prints the tools of each endpoint, then runs one catalog search through the same adapter the
store server uses and shows the products as the agent would see them.
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

from .backend import StoreToolError
from .shopify_backend import ShopifyCatalog, candidate_endpoints, mcp_rpc


async def probe(target: str, query: str) -> int:
    async with httpx.AsyncClient(timeout=20.0) as client:
        for url in candidate_endpoints(target):
            print(f"\n== {url}")
            try:
                tools = (await mcp_rpc(client, url, "tools/list")).get("tools", [])
            except (StoreToolError, httpx.HTTPError) as exc:
                print(f"   tools/list failed: {exc}")
                continue
            for tool in tools:
                props = tool.get("inputSchema", {}).get("properties", {})
                print(f"   - {tool.get('name')}({', '.join(props)})")

    catalog = ShopifyCatalog(target, profile_url=os.environ.get("UCP_AGENT_PROFILE_URL"))
    print(f"\n== search_products({query!r}) with profile {catalog._profile_url}")
    try:
        products = await catalog.search_products(query, limit=5)
    except (StoreToolError, httpx.HTTPError) as exc:
        print(f"   failed: {exc}")
        return 1
    print(f"   endpoint {catalog.endpoint}")
    for product in products:
        print(f"   - {product.sku:28} ${product.price_cents / 100:8.2f} {product.currency}  {product.name}  [{product.category}]")
    if products:
        detail = await catalog.get_product(products[0].sku)
        if detail:
            print(f"\n== get_product({products[0].sku!r}) description:\n   {detail.description[:400]}")
    return 0 if products else 1


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(probe(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "notebook")))


if __name__ == "__main__":
    main()
