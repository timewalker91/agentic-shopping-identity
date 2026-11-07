"""End-to-end gateway test: the full A10 story against a running stack.

  Terminal 1:  uv run python -m agentpass.dev_stub_store     (or Larry's store on :8100)
  Terminal 2:  uv run uvicorn agentpass.main:app --port 8000
  Terminal 3:  uv run python -m agentpass.smoke_gateway

Acts as the agent: gets a delegated token, then calls tools through the gateway
over MCP. Prints decisions only, never tokens.
"""

from __future__ import annotations

import asyncio
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from .config import BASE_URL
from .smoke import assertion, check, exchange, results

GATEWAY = f"{BASE_URL}/mcp"


async def call(s: ClientSession, tool: str, args: dict) -> tuple[str, str]:
    """Return (outcome, detail): outcome is 'allow' or the gateway's reason."""
    try:
        r = await s.call_tool(tool, args)
        text = r.content[0].text if r.content else ""
        return ("allow" if not r.isError else "store_error"), " ".join(text.split())[:400]
    except McpError as e:
        data = e.error.data or {}
        return data.get("reason", "error"), str({k: v for k, v in data.items() if k != "reason"})[:90]


async def run() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=20) as c:
        tok = c.post("/auth/login", json={"username": os.environ["DEMO_USERNAME"],
                                           "password": os.environ["DEMO_PASSWORD"]}).json()["access_token"]
        auth = {"Authorization": f"Bearer {tok}"}
        c.post("/demo/reset", headers=auth)
        sid = c.post("/sessions", headers=auth, json={}).json()["sid"]
        delegated = exchange(c, tok, sid, assertion()).json()["access_token"]
    cart = f"cart_{sid}"

    async with streamablehttp_client(GATEWAY, headers={"Authorization": f"Bearer {delegated}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            check("1. tools/list hides high-risk tools", "buy_gift_card" not in names and len(names) == 6, ", ".join(names))

            o, d = await call(s, "search_products", {"query": "notebook"})
            check("2. search allowed", o == "allow", d)
            o, d = await call(s, "add_to_cart", {"cart_id": cart, "sku": "SCH-101", "quantity": 2, "merchant": "freshmart"})
            check("3. add notebooks allowed", o == "allow", d)
            o, d = await call(s, "checkout", {"cart_id": cart, "ship_to_id": "addr_home"})
            check("4. checkout within budget allowed", o == "allow", d)

            big = f"{cart}_big"
            await call(s, "add_to_cart", {"cart_id": big, "sku": "SCH-666", "quantity": 4, "merchant": "freshmart"})
            o, d = await call(s, "checkout", {"cart_id": big, "ship_to_id": "addr_home"})
            check("5. over-budget checkout denied", o == "over_budget", d)

            o, d = await call(s, "get_product", {"sku": "SCH-666"})
            check("6. agent reads the poisoned listing", o == "allow" and "SYSTEM" in d, d[:60])
            o, d = await call(s, "add_to_cart", {"cart_id": cart, "sku": "GC-100", "quantity": 5, "merchant": "freshmart"})
            check("7. hijacked: gift cards denied by Cedar", o == "policy_denied", d)
            o, d = await call(s, "checkout", {"cart_id": cart, "ship_to_id": "addr_attacker"})
            check("8. hijacked: attacker address denied -> 2nd violation", o == "policy_denied", d)
            o, d = await call(s, "search_products", {"query": "pencils"})
            check("9. next call blocked: session auto-revoked", o == "session_revoked", d)

    with httpx.Client(timeout=20) as raw:  # the "copied token" replay, like curl
        rr = raw.post(GATEWAY, headers={"Authorization": f"Bearer {delegated}"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "search_products", "arguments": {"query": "milk"}}})
        err = rr.json().get("error", {}).get("data", {})
        check("10. replayed copied token blocked", err.get("reason") == "session_revoked" and err.get("status") == 401, str(err))
    with httpx.Client(base_url=BASE_URL, timeout=20) as c:
        r = exchange(c, tok, sid, assertion())
        check("11. no new token for revoked session", r.status_code == 400, r.json().get("error_description", ""))
        spent = c.get("/mandates/mdt_42", headers=auth).json()
        check("12. budget ledger updated", spent["spent"] > 0 and spent["held"] == 0, f"spent ${spent['spent']} of ${spent['max']}")
        ev = c.get(f"/sessions/{sid}/audit", headers=auth).json()["events"]
        print("\naudit:", " | ".join(f"{e['tool']}:{e['decision']}" for e in ev))

    print(f"\n{sum(ok for _, ok in results)}/{len(results)} checks passed")


if __name__ == "__main__":
    asyncio.run(run())
