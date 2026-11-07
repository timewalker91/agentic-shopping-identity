// AgentPass tool policies for shopping agents. Default deny: anything not permitted is denied,
// and a forbid always beats a permit. Same rules as the Architecture Design (AgentCore Policy).
// principal = the agent (attributes copied from its delegated token), action = the MCP tool,
// context.input = the tool's arguments.

// Read-only tools
permit (
  principal is Agent,
  action in [Action::"search_products", Action::"get_product", Action::"view_cart", Action::"get_order_status"],
  resource
) when { principal.tool_profile == "shopper_v1" };

// Add to cart: only the mandate's merchant, at most 10 of an item
permit (principal is Agent, action == Action::"add_to_cart", resource)
when {
  principal.tool_profile == "shopper_v1" &&
  context.input has merchant && context.input.merchant == principal.merchant &&
  context.input has quantity && context.input.quantity <= 10
};

// Never gift cards (scammers' favorite cash-out), whatever the prompt says
forbid (principal, action == Action::"add_to_cart", resource)
when { context.input has sku && context.input.sku like "GC-*" };

// Checkout: only to the shipping address bound in the token
permit (principal is Agent, action == Action::"checkout", resource)
when {
  principal.tool_profile == "shopper_v1" &&
  context.input has ship_to_id && context.input.ship_to_id == principal.ship_to_id
};

// High-risk tools are never granted to agents (also hidden from tools/list)
forbid (principal, action in [Action::"change_shipping_address", Action::"buy_gift_card"], resource);
