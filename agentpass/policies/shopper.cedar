// AgentPass tool policies. The same text drops into Amazon Bedrock AgentCore Policy unchanged.
//
//   principal = AgentPass::Agent::"<agent_id>"   attributes copied from the delegated token
//   action    = AgentPass::Action::"<mcp tool>"
//   resource  = AgentPass::Merchant::"<merchant in the token>"
//   context   = the validated tool arguments
//
// Cedar is default-deny: a tool with no permit (buy_gift_card, change_shipping_address,
// anything unknown) is denied even without a forbid. The forbids below make the demo rules
// explicit and show up by name in the audit log.

@id("shopper_v1_tools")
permit(
  principal,
  action in [
    AgentPass::Action::"search_products",
    AgentPass::Action::"get_product",
    AgentPass::Action::"view_cart",
    AgentPass::Action::"add_to_cart",
    AgentPass::Action::"checkout",
    AgentPass::Action::"get_order_status"
  ],
  resource
) when { principal.tool_profile == "shopper_v1" };

@id("no_hidden_tools")
forbid(
  principal,
  action in [AgentPass::Action::"change_shipping_address", AgentPass::Action::"buy_gift_card"],
  resource
);

@id("no_gift_card_skus")
forbid(principal, action == AgentPass::Action::"add_to_cart", resource)
when { context.sku like "GC-*" };

@id("quantity_cap_10")
forbid(principal, action == AgentPass::Action::"add_to_cart", resource)
when { context.quantity > 10 };

@id("merchant_must_match_token")
forbid(principal, action == AgentPass::Action::"add_to_cart", resource)
unless { context.merchant == principal.merchant };

@id("ship_to_must_match_token")
forbid(principal, action == AgentPass::Action::"checkout", resource)
unless { context.ship_to_id == principal.ship_to_id };
