"""Milestone 2 check: run the whole demo against the live API, several rounds, each after a reset.

    uv run python -m agent.demo_check              # 3 rounds, scripted agent only
    uv run python -m agent.demo_check --bedrock    # also one real Bedrock (Claude) purchase per round

Needs the store and the API running (see docs/demo-runbook.md). Uses its own agent state file, so it
doesn't touch the presenter's `agent login`. The password comes from AGENT_PASSWORD or
AGENTPASS_DEMO_PASSWORD (.env) and is never printed.

Each round checks the demo's required steps (docs/implementation-plan.md, section 3):
  1. authenticate the agent: passport names the user (sub) and the agent (act), 5-minute lifetime
  2. allow selected MCP tools: the agent sees only shopper tools
  3. normal purchase within budget is paid (scripted; plus the real Bedrock agent with --bedrock)
  4. over budget: the $89 item is denied, a cheaper cart goes through
  5. hijacked agent: gift card denied, attacker address denied, auto-revoke, next call refused
  6. after the revoke: a replay of the copied token gets 401 and a new token exchange is refused
  7. Stop button (Control API): the agent's next call is refused, timed
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

from agentpass.config import load_dotenv

from .broker import BrokerError, CredentialBroker
from .mcp_client import GatewayClient, GatewayError, SessionRevokedError
from .scripted import ScriptedShopper

SHOPPER_TOOLS = {"search_products", "get_product", "view_cart", "add_to_cart", "checkout", "get_order_status"}


class Check:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def __call__(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")
        return ok

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.results)


def _quiet(_: str) -> None:
    pass


def _session(broker: CredentialBroker) -> tuple[GatewayClient, dict]:
    broker.start_session(None)
    gateway = GatewayClient(broker)
    gateway.initialize()
    return gateway, broker.claims


def _scripted(broker: CredentialBroker, scenario: str) -> list:
    gateway, claims = _session(broker)
    shopper = ScriptedShopper(
        gateway,
        cart_id=f"cart_{claims['sid']}",
        merchant=claims["merchant"],
        ship_to_id=claims["ship_to_id"],
        out=_quiet,
    )
    return shopper.run(scenario)


def _outcomes(steps: list) -> list[tuple[str, str]]:
    return [(s.tool, s.outcome) for s in steps]


def run_round(broker: CredentialBroker, check: Check, bedrock: bool) -> None:
    broker.reset_demo()

    # 1-2. authenticate the agent; selected tools only
    gateway, claims = _session(broker)
    act = claims.get("act") or {}
    check(
        "agent authenticated: passport names user and agent",
        claims.get("sub") == broker.state.get("user_sub") and act.get("sub") == broker.agent_id,
        f"sub={claims.get('sub', '')[:8]}..., act={act.get('sub')}",
    )
    lifetime = int(claims.get("exp", 0)) - int(claims.get("iat", 0))
    check("passport lifetime is 5 minutes", lifetime == 300, f"{lifetime}s")
    tools = {t["name"] for t in gateway.list_tools()}
    check("agent sees only shopper tools", tools == SHOPPER_TOOLS, ", ".join(sorted(tools)))

    # 3. normal purchase (scripted)
    broker.reset_demo()
    steps = _scripted(broker, "happy")
    paid = [s for s in steps if s.tool == "checkout" and s.outcome == "ok" and s.result.get("status") == "paid"]
    bad = [s for s in steps if s.outcome != "ok"]
    check("normal purchase paid within budget", bool(paid) and not bad, f"${paid[0].result.get('total', 0):.2f}" if paid else _outcomes(steps))

    # 3b. the real Bedrock agent
    if bedrock:
        broker.reset_demo()
        gateway, claims = _session(broker)
        from .shopper import BedrockShopper

        shopper = BedrockShopper(
            gateway,
            model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            cart_id=f"cart_{claims['sid']}",
            merchant=claims["merchant"],
            ship_to_id=claims["ship_to_id"],
            budget=broker.mandate().get("remaining", 0.0),
        )
        started = time.monotonic()
        stdout = sys.stdout
        try:
            sys.stdout = open(os.devnull, "w")  # the shopper prints its steps; keep the report short
            shopper.run("Buy a pack of notebooks and a set of pens for school. Stay under budget.")
        finally:
            sys.stdout.close()
            sys.stdout = stdout
        events = broker.audit()
        paid_events = [e for e in events if e["tool"] == "checkout" and e["decision"] == "allow"]
        denied = [e for e in events if e["decision"] != "allow" and e["tool"] not in ("token_exchange",)]
        check(
            "real Bedrock agent completes a purchase",
            bool(paid_events) and not denied,
            f"{paid_events[0]['reason'] if paid_events else 'no checkout'}, {time.monotonic() - started:.0f}s",
        )

    # 4. over budget
    broker.reset_demo()
    steps = _scripted(broker, "over_budget")
    checkouts = [s for s in steps if s.tool == "checkout"]
    check(
        "over budget: $89 item denied, cheaper cart paid",
        len(checkouts) == 2 and checkouts[0].outcome == "denied" and checkouts[1].outcome == "ok",
        " -> ".join(f"{s.tool}:{s.outcome}" for s in checkouts),
    )

    # 5. hijacked agent
    broker.reset_demo()
    steps = _scripted(broker, "hijacked")
    expected = [("add_to_cart", "denied"), ("checkout", "denied"), ("view_cart", "revoked")]
    check("hijacked: gift card denied, address denied, then revoked", _outcomes(steps)[-3:] == expected, ", ".join(f"{t}:{o}" for t, o in _outcomes(steps)[-3:]))
    session = broker.session()
    check("session auto-revoked", session.get("status") == "revoked", session.get("revoke_reason", ""))

    # 6. replay the copied token; try to get a new one
    token = broker.token_for_replay()
    left = broker.seconds_to_expiry
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "view_cart", "arguments": {"cart_id": "cart_replay"}}}
    resp = httpx.post(f"{broker.api_url}/mcp", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    check("replay of the copied token gets 401", resp.status_code == 401, f"HTTP {resp.status_code}, token still had {left}s left")
    try:
        broker.exchange()
        check("new token exchange refused after revoke", False, "a new passport was issued")
    except BrokerError as exc:
        check("new token exchange refused after revoke", True, f"{exc.status} {exc.reason}")

    # 7. Stop button
    broker.reset_demo()
    gateway, claims = _session(broker)
    gateway.call_tool("search_products", {"query": "pens"})
    stopped_at = time.monotonic()
    broker.stop()
    try:
        gateway.call_tool("view_cart", {"cart_id": f"cart_{claims['sid']}"})
        check("Stop: next call refused", False, "the call was allowed")
    except SessionRevokedError as exc:
        ms = (time.monotonic() - stopped_at) * 1000
        check("Stop: next call refused", exc.status == 401, f"{exc.status} in {ms:.0f} ms, token still had {broker.seconds_to_expiry}s left")
    except GatewayError as exc:
        check("Stop: next call refused", False, f"{exc.status} {exc.reason}")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Milestone 2: run the full demo N times after a reset")
    parser.add_argument("--api", default=os.environ.get("AGENTPASS_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--bedrock", action="store_true", help="also run one real Bedrock purchase per round")
    args = parser.parse_args()

    user = os.environ.get("AGENT_USER", "parent")
    password = os.environ.get("AGENT_PASSWORD") or os.environ.get("AGENTPASS_DEMO_PASSWORD")
    if not password:
        print("set AGENT_PASSWORD or AGENTPASS_DEMO_PASSWORD (.env)", file=sys.stderr)
        return 2

    state = Path(tempfile.mkdtemp(prefix="demo_check_")) / "agent_state.json"
    broker = CredentialBroker(args.api, state_path=state)
    broker.login(user, password)

    rounds_passed = 0
    for n in range(1, args.rounds + 1):
        print(f"round {n}/{args.rounds}")
        check = Check()
        try:
            run_round(broker, check, args.bedrock)
        except Exception as exc:  # report and keep going: the point is to see every failure
            check(f"round crashed: {type(exc).__name__}", False, str(exc)[:200])
        rounds_passed += check.passed
    broker.reset_demo()
    state.unlink(missing_ok=True)
    print(f"\n{rounds_passed}/{args.rounds} rounds passed{' (M2 passes)' if rounds_passed == args.rounds else ''}; demo reset for the next run")
    return 0 if rounds_passed == args.rounds else 1


if __name__ == "__main__":
    sys.exit(main())
