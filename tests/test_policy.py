"""One test per Cedar / argument rule (plan, section 5, MCP tools table)."""
import pytest

from agentpass.policy import InvalidArguments, PolicyEngine, validate_args

CLAIMS = {
    "agent_id": "agent_shopper_v1",
    "sub": "user-1",
    "tool_profile": "shopper_v1",
    "merchant": "freshmart",
    "ship_to_id": "addr_home",
    "sid": "sess_test",
}


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine.load_default()


def decide(engine: PolicyEngine, tool: str, args: dict):
    return engine.authorize(CLAIMS, tool, validate_args(tool, args))


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_products", {"query": "pens"}),
        ("search_products", {"query": "pens", "category": "stationery"}),
        ("get_product", {"sku": "SKU-MRK-050"}),
        ("view_cart", {"cart_id": "c1"}),
        ("add_to_cart", {"cart_id": "c1", "sku": "SKU-PEN-010", "quantity": 10, "merchant": "freshmart"}),
        ("checkout", {"cart_id": "c1", "ship_to_id": "addr_home"}),
        ("get_order_status", {"order_id": "ord_1"}),
    ],
)
def test_shopper_tools_are_permitted(engine, tool, args):
    decision = decide(engine, tool, args)
    assert decision.allowed, decision.reason
    assert decision.reason == "permit:shopper_v1_tools"


def test_gift_card_sku_is_forbidden(engine):
    decision = decide(engine, "add_to_cart", {"cart_id": "c1", "sku": "GC-100", "quantity": 1, "merchant": "freshmart"})
    assert not decision.allowed
    assert "no_gift_card_skus" in decision.reason


def test_quantity_over_ten_is_forbidden(engine):
    decision = decide(engine, "add_to_cart", {"cart_id": "c1", "sku": "SKU-PEN-010", "quantity": 11, "merchant": "freshmart"})
    assert not decision.allowed
    assert "quantity_cap_10" in decision.reason


def test_merchant_must_match_token(engine):
    decision = decide(engine, "add_to_cart", {"cart_id": "c1", "sku": "SKU-PEN-010", "quantity": 1, "merchant": "othermart"})
    assert not decision.allowed
    assert "merchant_must_match_token" in decision.reason


def test_checkout_to_other_address_is_forbidden(engine):
    decision = decide(engine, "checkout", {"cart_id": "c1", "ship_to_id": "addr_4471_warehouse"})
    assert not decision.allowed
    assert "ship_to_must_match_token" in decision.reason


@pytest.mark.parametrize("tool", ["buy_gift_card", "change_shipping_address"])
def test_hidden_tools_are_forbidden_for_all_agents(engine, tool):
    decision = decide(engine, tool, {"cart_id": "c1", "ship_to_id": "addr_home", "amount": 100})
    assert not decision.allowed
    assert "no_hidden_tools" in decision.reason


def test_unknown_tool_has_no_permit(engine):
    decision = decide(engine, "delete_account", {})
    assert not decision.allowed
    assert "no permit" in decision.reason


def test_other_profile_gets_nothing(engine):
    claims = {**CLAIMS, "tool_profile": "reader_v1"}
    decision = engine.authorize(claims, "search_products", {"query": "pens"})
    assert not decision.allowed


def test_missing_argument_is_rejected_before_cedar():
    with pytest.raises(InvalidArguments):
        validate_args("add_to_cart", {"cart_id": "c1", "quantity": 1, "merchant": "freshmart"})
    with pytest.raises(InvalidArguments):
        validate_args("add_to_cart", {"cart_id": "c1", "sku": "SKU-1", "quantity": 0, "merchant": "freshmart"})


def test_cedar_evaluation_error_fails_closed(engine):
    # Without validation Cedar would skip the erroring forbid and allow; the engine must deny.
    decision = engine.authorize(CLAIMS, "add_to_cart", {"cart_id": "c1", "quantity": 1, "merchant": "freshmart"})
    assert not decision.allowed
    assert "evaluation error" in decision.reason


def test_visible_tools_hide_forbidden_ones(engine):
    visible = engine.visible_tools("shopper_v1")
    assert set(visible) == {"search_products", "get_product", "view_cart", "add_to_cart", "checkout", "get_order_status"}
    assert engine.visible_tools("unknown_profile") == ()
