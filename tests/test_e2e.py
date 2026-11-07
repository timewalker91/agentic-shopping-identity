"""End to end: login -> session -> passport -> gateway -> Cedar -> budget -> store -> audit.

Covers the four A10 steps: authenticate the agent, allow selected tools, revoke mid-task
(Stop and auto-revoke), block subsequent requests (401 on the next call, on a replayed token
and on a new token exchange).
"""
from __future__ import annotations

from agent.broker import CredentialBroker
from agent.mcp_client import GatewayClient
from agent.scripted import ScriptedShopper
from tests.conftest import Harness, error_of, result_of

SHOPPER_TOOLS = {"search_products", "get_product", "view_cart", "add_to_cart", "checkout", "get_order_status"}


def test_login_session_and_passport_claims(harness: Harness):
    sid, token, claims = harness.passport()
    assert sid.startswith("sess_") and token.count(".") == 2
    assert claims["iss"] == "agentpass" and claims["aud"] == "agentpass-gateway"
    assert claims["act"] == {"sub": "agent_shopper_v1", "sub_profile": "ai_agent"}
    assert claims["sid"] == sid and claims["mandate_id"] == harness.mandate_id
    assert claims["tool_profile"] == "shopper_v1" and claims["merchant"] == "freshmart"
    assert claims["ship_to_id"] == "addr_home" and claims["exp"] - claims["iat"] == 300
    session = harness.http.get(f"/sessions/{sid}", headers=harness.user_headers).json()
    assert session["passport"]["jti"] == claims["jti"]
    assert session["budget"]["max"] == 50.0


def test_jwks_publishes_the_signing_key(harness: Harness):
    keys = harness.http.get("/.well-known/jwks.json").json()["keys"]
    assert keys and keys[0]["kty"] == "EC" and keys[0]["alg"] == "ES256"


def test_tools_list_shows_only_shopper_tools(harness: Harness):
    _, token, _ = harness.passport()
    init = harness.rpc(token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "agentpass-gateway"
    tools = harness.rpc(token, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == SHOPPER_TOOLS
    assert all("inputSchema" in t for t in tools)


def test_missing_or_invalid_token_is_rejected(harness: Harness):
    assert harness.rpc(None, "tools/list").status_code == 401
    resp = harness.rpc("not.a.token", "tools/list")
    assert resp.status_code == 401 and error_of(resp)[0] == "invalid_token"


def test_happy_path_checkout_within_budget(harness: Harness):
    sid, token, _ = harness.passport()
    found = result_of(harness.call(token, "search_products", {"query": "school supplies"}))
    skus = [p["sku"] for p in found["products"]]
    assert "SKU-NB-001" in skus and "GC-100" not in skus

    for sku in ("SKU-NB-001", "SKU-PEN-010"):
        result_of(harness.call(token, "add_to_cart", {"cart_id": "cart_a", "sku": sku, "quantity": 1, "merchant": "freshmart"}))
    cart = result_of(harness.call(token, "view_cart", {"cart_id": "cart_a"}))
    assert cart["total"] == 13.48

    order = result_of(harness.call(token, "checkout", {"cart_id": "cart_a", "ship_to_id": "addr_home"}))
    assert order["status"] == "paid" and order["total"] == 13.48
    assert order["budget"]["spent"] == 13.48 and order["budget"]["remaining"] == 36.52
    status = result_of(harness.call(token, "get_order_status", {"order_id": order["order_id"]}))
    assert status["status"] == "confirmed"

    mandate = harness.mandate()
    assert mandate["spent"] == 13.48 and mandate["held"] == 0.0

    events = harness.audit(sid)
    checkout_events = [e for e in events if e["tool"] == "checkout"]
    assert checkout_events[-1]["decision"] == "allow" and checkout_events[-1]["layer"] == "budget"
    assert all(e["args_digest"].startswith("sha256:") for e in events)
    assert events[0]["tool"] == "token_exchange" and events[0]["layer"] == "token"


def test_over_budget_checkout_is_denied_server_side(harness: Harness):
    _, token, _ = harness.passport()
    result_of(harness.call(token, "add_to_cart", {"cart_id": "cart_b", "sku": "SKU-HDPH-9", "quantity": 1, "merchant": "freshmart"}))
    resp = harness.call(token, "checkout", {"cart_id": "cart_b", "ship_to_id": "addr_home"})
    assert resp.status_code == 403
    reason, data = error_of(resp)
    assert reason == "over_budget" and data["remaining"] == 50.0 and data["total"] == 89.0
    assert harness.mandate()["held"] == 0.0
    # the agent is not revoked by a budget denial: a cheaper cart still works
    result_of(harness.call(token, "add_to_cart", {"cart_id": "cart_c", "sku": "SKU-GLUE-02", "quantity": 1, "merchant": "freshmart"}))
    assert result_of(harness.call(token, "checkout", {"cart_id": "cart_c", "ship_to_id": "addr_home"}))["status"] == "paid"


def test_hijacked_agent_is_auto_revoked_and_blocked(harness: Harness):
    sid, token, _ = harness.passport()
    listing = result_of(harness.call(token, "get_product", {"sku": "SKU-MRK-050"}))
    assert "GC-100" in listing["product"]["description"]

    resp = harness.call(token, "add_to_cart", {"cart_id": "cart_h", "sku": "GC-100", "quantity": 5, "merchant": "freshmart"})
    assert resp.status_code == 403
    reason, data = error_of(resp)
    assert reason == "policy_denied" and "no_gift_card_skus" in data["policy"] and not data.get("auto_revoked")

    resp = harness.call(token, "checkout", {"cart_id": "cart_h", "ship_to_id": "addr_4471_warehouse"})
    assert resp.status_code == 403
    reason, data = error_of(resp)
    assert reason == "policy_denied" and "ship_to_must_match_token" in data["policy"] and data["auto_revoked"] is True

    # next call with the same, unexpired token
    resp = harness.call(token, "view_cart", {"cart_id": "cart_h"})
    assert resp.status_code == 401 and error_of(resp)[0] == "session_revoked"
    # a replay of the copied token
    assert harness.call(token, "search_products", {"query": "pens"}).status_code == 401
    # and a fresh exchange
    resp = harness.exchange(sid)
    assert resp.status_code == 401 and resp.json()["reason"] == "session_revoked"

    events = harness.audit(sid)
    decisions = [(e["decision"], e["layer"]) for e in events if e["tool"] != "token_exchange"]
    assert decisions[:5] == [("allow", "policy"), ("deny", "policy"), ("deny", "policy"), ("revoked", "risk"), ("revoked", "revocation")]
    assert harness.http.get(f"/sessions/{sid}", headers=harness.user_headers).json()["status"] == "revoked"


def test_stop_button_blocks_next_call_and_new_tokens(harness: Harness):
    sid, token, _ = harness.passport()
    result_of(harness.call(token, "search_products", {"query": "pens"}))
    stopped = harness.stop(sid)
    assert stopped["status"] == "revoked"
    resp = harness.call(token, "search_products", {"query": "pens"})
    assert resp.status_code == 401 and error_of(resp)[0] == "session_revoked"
    assert harness.exchange(sid).status_code == 401
    assert [e["decision"] for e in harness.audit(sid)][-2:] == ["revoked", "revoked"]


def test_hidden_tool_call_is_denied(harness: Harness):
    _, token, _ = harness.passport()
    resp = harness.call(token, "buy_gift_card", {"amount": 100, "recipient_email": "x@example.com"})
    assert resp.status_code == 403 and "no_hidden_tools" in error_of(resp)[1]["policy"]
    resp = harness.call(token, "add_to_cart", {"cart_id": "c", "sku": "SKU-PEN-010", "quantity": 11, "merchant": "freshmart"})
    assert resp.status_code == 403 and "quantity_cap_10" in error_of(resp)[1]["policy"]


def test_bad_arguments_are_rejected_before_policy(harness: Harness):
    _, token, _ = harness.passport()
    resp = harness.call(token, "add_to_cart", {"cart_id": "c", "quantity": 1, "merchant": "freshmart"})
    assert resp.status_code == 400 and error_of(resp)[0] == "invalid_arguments"


def test_store_errors_come_back_as_tool_errors(harness: Harness):
    _, token, _ = harness.passport()
    resp = harness.call(token, "add_to_cart", {"cart_id": "c", "sku": "SKU-NOPE", "quantity": 1, "merchant": "freshmart"})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["isError"] is True and result["structuredContent"]["error"] == "unknown_sku"


def test_scripted_agent_happy_and_hijacked(harness: Harness, state_path, api_url):
    broker = CredentialBroker(api_url, state_path=state_path)
    broker.login("parent", "parent-pass")
    broker.start_session()
    gateway = GatewayClient(broker)
    gateway.initialize()
    claims = broker.claims
    lines: list[str] = []
    steps = ScriptedShopper(gateway, cart_id="cart_s1", merchant=claims["merchant"], ship_to_id=claims["ship_to_id"], out=lines.append).run("happy")
    assert [s.outcome for s in steps] == ["ok"] * len(steps) and steps[-1].tool == "get_order_status"

    steps = ScriptedShopper(gateway, cart_id="cart_s2", merchant=claims["merchant"], ship_to_id=claims["ship_to_id"], out=lines.append).run("hijacked")
    assert [(s.tool, s.outcome) for s in steps] == [
        ("search_products", "ok"),
        ("get_product", "ok"),
        ("add_to_cart", "denied"),
        ("checkout", "denied"),
        ("view_cart", "revoked"),
    ]
    assert steps[-1].detail.startswith("401 session_revoked")
    assert state_path.stat().st_mode & 0o777 == 0o600
