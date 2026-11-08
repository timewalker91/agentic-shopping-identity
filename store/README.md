# store/

**Owners:** Jack (mock store code), Larry (Shopify dev-store adapter and `seed.json` with the poisoned listing).

Store MCP server on `:8100` (mcp SDK `MCPServer`, Streamable HTTP, stateless, JSON responses). Only the
gateway may call it: it binds to `127.0.0.1` and enforces nothing itself. Checkout always goes to the
payment mock, so no real payment happens with either backend.

```bash
python -m store.server                       # mock catalog from seed.json
STORE_BACKEND=shopify SHOPIFY_STORE_DOMAIN=<shop>.myshopify.com python -m store.server
python -m store.shopify_probe <shop>.myshopify.com "notebook"   # spike: what does the store's MCP endpoint offer?
curl -s localhost:8100/health
```

## Tools (contract, plan section 5)

| Tool | Input | Result |
|---|---|---|
| `search_products` | `query`, `category?` | `{products:[{sku, name, price, currency, category, merchant}]}` |
| `get_product` | `sku` | `{product:{..., description}}` (SKU-MRK-050 carries the hidden instruction) |
| `view_cart` | `cart_id` | `{cart_id, items, item_count, total, currency}`; unknown ids are empty carts |
| `add_to_cart` | `cart_id, sku, quantity, merchant` | the updated cart + `added` |
| `checkout` | `cart_id, ship_to_id` | `{status: "paid", order_id, total, ...}` or `{status: "declined", reason}` |
| `get_order_status` | `order_id` | the order |
| `change_shipping_address`, `buy_gift_card` | | exist here on purpose; the gateway never permits them |
| `reset_store` | | demo reset (called by `POST /demo/reset` on agentpass) |

Store-level failures are MCP tool errors with text `code: message` (`unknown_sku`, `empty_cart`,
`wrong_merchant`, `unknown_order`); the gateway passes them to the agent as `isError` results.

## Backends

- `mock_backend.py`: deterministic catalog from `seed.json` (merchant `freshmart`, 10 products, gift cards `GC-025`/`GC-100`, the poisoned markers listing, $89 headphones for the over-budget run).
- `shopify_backend.py`: catalog from a real Shopify store through its **UCP MCP endpoint**
  `https://<shop>/api/ucp/mcp` (tools `search_catalog`, `lookup_catalog`, `get_product`; the older
  `search_shop_catalog` on `/api/mcp` is gone from live stores but still handled). Verified live against a
  public Shopify store: search, product detail, add to cart, checkout through the payment mock.
  Every UCP call must name a public HTTPS **agent profile** URL in `meta.ucp-agent.profile`; Shopify fetches it
  ("UCP discovery") and answers `profile_unreachable` otherwise. Default: Shopify's hosted test fixture
  (`https://shopify.dev/ucp/agent-profiles/2026-08-25/valid-with-capabilities.json`). To use our own, host
  `docs/ucp-agent-profile.json` at a public URL (for example the GitHub raw URL once it is pushed) and set
  `UCP_AGENT_PROFILE_URL`. Prices arrive in minor units and are mapped to the same `price` dollars the mock uses.

  Preparing the dev store for the demo (Shopify admin):
  1. Give every variant a SKU. The gift card product gets SKU `GC-100` (any `GC-*` trips the Cedar rule; a
     product titled "Gift Card" without a SKU is prefixed `GC-` automatically).
  2. Put the hidden instruction in the markers product description as visible text or an HTML comment; the
     adapter keeps comment text so the poisoned listing reaches the agent.
  3. Keep a few products under the $50 demo budget and one around $89 for the over-budget run.
  4. The merchant name in the token stays `freshmart` (`STORE_MERCHANT` to change it; the mandate must match).
  Then: `STORE_BACKEND=shopify SHOPIFY_STORE_DOMAIN=<shop>.myshopify.com python -m store.server`.
  Check first with `python -m store.shopify_probe <shop>.myshopify.com "markers"`.
- Carts and orders are kept in this process for both backends (`backend.py`); the payment mock (`payment.py`) starts with `PAYMENT_MOCK_FUNDS` dollars and answers `insufficient_funds` when they run out.

## Real Shopify checkout (`STORE_CHECKOUT=shopify`)

With `STORE_BACKEND=shopify STORE_CHECKOUT=shopify`, `checkout` turns the local cart into a real
Shopify checkout through the store's Checkout MCP (`create_checkout`), then returns
`status: "awaiting_buyer"` with a `continue_url`. **The parent pays on Shopify's checkout page**:
Shopify does not let self-serve agents call `complete_checkout` (it answers
`checkout_completion_ineligible` unless Shopify has granted the client that permission and the
merchant has enabled the agent's channel), so the `continue_url` handoff is the supported path.
On a dev store with the Bogus gateway the result is a real *test* order in Shopify admin.

- `ship_to_id` is resolved server-side from the address book (`address_book.py`): the agent and
  the model only see ids like `addr_home`, never the contact or street address. Put real test
  data in `store/address_book.json` (gitignored) or point `SHOPIFY_ADDRESS_BOOK` at a file;
  otherwise `address_book.example.json` (placeholder data) is used.
- The gateway counts a handoff against the mandate (settles the hold), so a hijacked agent cannot
  stack up payment links beyond the budget. If the parent never pays, that amount stays spent
  until `agent demo reset`.
- `get_order_status` refreshes from Shopify (`get_checkout`): `awaiting_buyer` becomes
  `paid_by_buyer` once the parent completes payment, or `canceled` when the checkout expires.
- Shopify's total can add shipping or tax on top of the cart; the result carries `shopify_total`
  and a note when it differs. The budget is held on the cart total.
