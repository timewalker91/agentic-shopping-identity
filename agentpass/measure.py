"""Measure the numbers for the pitch, against a running stack (stub or real store).

  uv run python -m agentpass.measure

- token exchange latency
- gateway overhead per allowed tool call (checks only, from the audit log) and round trip
- Stop -> next call blocked: time from pressing Stop to the agent's next call being refused
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError

from .config import BASE_URL
from .smoke import assertion, exchange

GATEWAY = f"{BASE_URL}/mcp"


def ms(xs: list[float]) -> str:
    return f"median {statistics.median(xs):.0f} ms (min {min(xs):.0f}, max {max(xs):.0f}, n={len(xs)})"


async def run() -> None:
    c = httpx.Client(base_url=BASE_URL, timeout=20)
    tok = c.post("/auth/login", json={"username": os.environ["DEMO_USERNAME"],
                                       "password": os.environ["DEMO_PASSWORD"]}).json()["access_token"]
    auth = {"Authorization": f"Bearer {tok}"}
    c.post("/demo/reset", headers=auth)
    sid = c.post("/sessions", headers=auth, json={}).json()["sid"]

    ex = []
    for _ in range(5):
        t = time.perf_counter(); r = exchange(c, tok, sid, assertion()); ex.append((time.perf_counter() - t) * 1000)
    delegated = r.json()["access_token"]

    rt, stop_to_block = [], []
    async with streamablehttp_client(GATEWAY, headers={"Authorization": f"Bearer {delegated}"}) as (rd, wr, _):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            for _ in range(10):
                t = time.perf_counter(); await s.call_tool("search_products", {"query": "pencil"})
                rt.append((time.perf_counter() - t) * 1000)
            t0 = time.perf_counter()
            c.post(f"/sessions/{sid}/stop", headers=auth)
            try:
                await s.call_tool("search_products", {"query": "pencil"})
                blocked = False
            except McpError as e:
                blocked = (e.error.data or {}).get("reason") == "session_revoked"
            stop_to_block.append((time.perf_counter() - t0) * 1000)

    events = c.get(f"/sessions/{sid}/audit", headers=auth).json()["events"]
    overhead = [e["latency_ms"] for e in events if e["tool"] == "search_products" and e["decision"] == "allow"]
    print(f"token exchange (KMS sign + 5 checks): {ms(ex)}")
    print(f"gateway, server side (checks + store): {ms([float(x) for x in overhead])}")
    print(f"full round trip agent->gateway->store: {ms(rt)}")
    print(f"Stop pressed -> next call refused:     {stop_to_block[0]:.0f} ms, blocked={blocked}")
    left = jwt.decode(delegated, options={"verify_signature": False})["exp"] - time.time()
    print(f"token time left when it was refused:   {left:.0f}s of 300s")


if __name__ == "__main__":
    asyncio.run(run())
