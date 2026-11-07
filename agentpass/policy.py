"""Cedar policy evaluation for MCP tool calls (cedarpy).

Fail closed: Cedar skips a policy that errors while evaluating (for example a forbid that
reads context.sku when sku is missing). We therefore validate arguments with pydantic first
and treat any evaluation error as a deny.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cedarpy
from pydantic import BaseModel, ConfigDict, Field, ValidationError

POLICY_FILE = Path(__file__).with_name("policies") / "shopper.cedar"
NAMESPACE = "AgentPass"

# Which tools each profile may see in tools/list. Cedar decides each call; this list only
# keeps hidden tools out of the model's view.
VISIBLE_TOOLS: dict[str, tuple[str, ...]] = {
    "shopper_v1": ("search_products", "get_product", "view_cart", "add_to_cart", "checkout", "get_order_status"),
}


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class SearchProductsArgs(_Args):
    query: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=50)


class GetProductArgs(_Args):
    sku: str = Field(min_length=1, max_length=100)


class ViewCartArgs(_Args):
    cart_id: str = Field(min_length=1, max_length=100)


class AddToCartArgs(_Args):
    cart_id: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1)
    merchant: str = Field(min_length=1, max_length=50)


class CheckoutArgs(_Args):
    cart_id: str = Field(min_length=1, max_length=100)
    ship_to_id: str = Field(min_length=1, max_length=100)


class GetOrderStatusArgs(_Args):
    order_id: str = Field(min_length=1, max_length=100)


TOOL_ARGS: dict[str, type[_Args]] = {
    "search_products": SearchProductsArgs,
    "get_product": GetProductArgs,
    "view_cart": ViewCartArgs,
    "add_to_cart": AddToCartArgs,
    "checkout": CheckoutArgs,
    "get_order_status": GetOrderStatusArgs,
}


class InvalidArguments(ValueError):
    pass


def validate_args(tool: str, args: dict) -> dict:
    """Return the cleaned arguments for a known tool. Unknown tools pass through scalar args only."""
    if not isinstance(args, dict):
        raise InvalidArguments("arguments must be an object")
    model = TOOL_ARGS.get(tool)
    if model is None:
        return {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))}
    try:
        return model.model_validate(args).model_dump(exclude_none=True)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise InvalidArguments(problems) from exc


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    policies: tuple[str, ...] = ()


class PolicyEngine:
    def __init__(self, policy_text: str, visible_tools: dict[str, tuple[str, ...]] | None = None) -> None:
        self._policies = cedarpy.PolicySet.from_str(policy_text)
        self._visible = visible_tools or VISIBLE_TOOLS
        # cedarpy reports policies positionally (policy0, policy1, ...); map them to the @id names
        self._ids = re.findall(r'@id\("([^"]+)"\)', policy_text)

    def _name(self, policy_ref: str) -> str:
        if policy_ref.startswith("policy") and policy_ref[6:].isdigit():
            index = int(policy_ref[6:])
            if index < len(self._ids):
                return self._ids[index]
        return policy_ref

    @classmethod
    def load_default(cls) -> "PolicyEngine":
        return cls(POLICY_FILE.read_text())

    def visible_tools(self, profile: str) -> tuple[str, ...]:
        return self._visible.get(profile, ())

    def authorize(self, claims: dict, tool: str, args: dict) -> PolicyDecision:
        agent_id = claims["agent_id"]
        merchant = claims["merchant"]
        entities = [
            {
                "uid": {"type": f"{NAMESPACE}::Agent", "id": agent_id},
                "attrs": {
                    "agent_id": agent_id,
                    "user": claims["sub"],
                    "tool_profile": claims["tool_profile"],
                    "merchant": merchant,
                    "ship_to_id": claims["ship_to_id"],
                    "sid": claims["sid"],
                },
                "parents": [],
            },
            {"uid": {"type": f"{NAMESPACE}::Merchant", "id": merchant}, "attrs": {}, "parents": []},
        ]
        request = {
            "principal": {"type": f"{NAMESPACE}::Agent", "id": agent_id},
            "action": {"type": f"{NAMESPACE}::Action", "id": tool},
            "resource": {"type": f"{NAMESPACE}::Merchant", "id": merchant},
            "context": {**args, "tool": tool},
        }
        result = cedarpy.is_authorized(request, self._policies, entities)
        reasons = tuple(self._name(r) for r in result.diagnostics.reasons)
        errors = list(result.diagnostics.errors)
        if errors:
            return PolicyDecision(False, "policy evaluation error: " + "; ".join(errors), reasons)
        if result.decision == cedarpy.Decision.Allow:
            return PolicyDecision(True, "permit:" + ",".join(reasons), reasons)
        if reasons:
            return PolicyDecision(False, "forbid:" + ",".join(reasons), reasons)
        return PolicyDecision(False, f"no permit for tool '{tool}' in profile {claims['tool_profile']}", reasons)
