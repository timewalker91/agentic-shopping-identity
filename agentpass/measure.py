"""Measure the pitch numbers against the running stack (any mode).

  uv run python -m agent login --user parent        # once
  uv run python -m agentpass.measure

Prints: token exchange time (agent assertion + passport signing), allowed tool-call round trip,
and the time from pressing Stop to the agent's next call being refused (with token time left).
"""
from __future__ import annotations

import os
import statistics
import time

import httpx

from agent.broker import CredentialBroker


def main() -> None:
    api = os.environ.get("AGENTPASS_API_URL", "http://127.0.0.1:8000")
    broker = CredentialBroker(api)
    http = httpx.Client(base_url=api, timeout=30)

    def call(tool: str, args: dict) -> tuple[httpx.Response, float]:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        t = time.perf_counter()
        resp = http.post("/mcp", json=body, headers=broker.auth_headers())
        return resp, (time.perf_counter() - t) * 1000

    broker.start_session()
    t = time.perf_counter()
    broker.exchange()
    exchange_ms = (time.perf_counter() - t) * 1000

    rtt = [call("search_products", {"query": "notebook"})[1] for _ in range(8)]

    t0 = time.perf_counter()
    broker.stop()
    resp, _ = call("search_products", {"query": "notebook"})
    stop_ms = (time.perf_counter() - t0) * 1000
    reason = (resp.json().get("error") or {}).get("data", {}).get("reason") or resp.json().get("reason")

    print(f"token exchange (signed agent assertion + passport): {exchange_ms:.0f} ms")
    print(f"allowed tool call round trip: median {statistics.median(rtt):.0f} ms (n={len(rtt)})")
    print(f"Stop -> next call refused: {stop_ms:.0f} ms (HTTP {resp.status_code}, {reason})")
    print(f"token time left when refused: {broker.seconds_to_expiry}s of 300s")


if __name__ == "__main__":
    main()
