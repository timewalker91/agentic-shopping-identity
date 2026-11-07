# store/

**Owners:** Jack (mock store code), Larry (Shopify dev-store adapter if chosen at 16:00, and the `seed.json` catalog with the poisoned listing).
Store MCP server on `:8100` (only the gateway may call it) + payment mock with a simulated `insufficient_funds`. Same tool contract (`docs/implementation-plan.md`, section 5) whether it runs as the mock or as the Shopify adapter. Checkout always goes to the payment mock, so no real payments happen.
