import type { AuditEvent } from "./api";

// Plain-English labels for the gateway's decision reasons. The raw reason is shown underneath.
const RULES: Array<[RegExp, string]> = [
  [/no_gift_card_skus/, "Gift cards are never allowed"],
  [/quantity_cap_10/, "More than 10 of one item"],
  [/merchant_must_match_token/, "Store isn't in the mandate"],
  [/ship_to_must_match_token/, "Shipping address doesn't match the mandate"],
  [/no_hidden_tools|default.?deny|not.?permitted/, "Tool isn't allowed for this agent"],
  [/auto_revoke/, "Agent revoked automatically: repeated policy violations"],
  [/user_stop/, "Stopped by the parent"],
  [/revoked \(|session_revoked|session is revoked|no new passport/, "Session revoked: call refused"],
  [/over_budget|budget/, "Over the mandate's budget"],
  [/passport issued/, "Agent Passport issued"],
  [/agent authentication failed/, "Agent couldn't prove its identity"],
  [/mandate_inactive|mandate inactive/, "Mandate isn't active"],
  [/token_expired/, "Passport expired"],
  [/invalid_token/, "Invalid passport"],
  [/^permit:/, "Allowed by policy"],
];

export function plainReason(e: AuditEvent): string {
  // A call that hit an already-revoked session (checked before the rules: its reason repeats the revoke cause).
  if (e.decision === "revoked" && e.layer === "revocation" && e.tool !== "stop") return "Refused: session already revoked";
  if (e.decision === "revoked" && e.layer === "risk") return "Agent revoked automatically: 2 policy violations";
  const paid = /^paid (\$[\d.,]+)/.exec(e.reason);
  if (paid) return `Paid ${paid[1]}, within budget`;
  for (const [re, label] of RULES) if (re.test(e.reason)) return label;
  if (e.decision === "allow") return "Allowed";
  return e.reason;
}

export const TOOL_NAMES: Record<string, string> = {
  search_products: "Search products",
  get_product: "Read product page",
  view_cart: "View cart",
  add_to_cart: "Add to cart",
  checkout: "Checkout",
  get_order_status: "Order status",
  token_exchange: "Passport request",
  stop: "Stop pressed",
  change_shipping_address: "Change shipping address",
  buy_gift_card: "Buy gift card",
};

export function toolName(tool: string): string {
  return TOOL_NAMES[tool] ?? tool;
}

/** Tool calls made by the agent (not the passport request or the Stop button). */
export function isAgentCall(e: AuditEvent): boolean {
  return e.tool !== "token_exchange" && e.tool !== "stop";
}

export function clock(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? ts
    : d.toLocaleTimeString("en-US", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}
