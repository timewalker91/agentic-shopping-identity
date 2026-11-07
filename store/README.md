# store/

**Owner:** Larry. Store MCP server on `:8100` (only the gateway may call it): the mock store, or an adapter to a Shopify dev store (decision at 16:00). Includes the payment mock with a simulated `insufficient_funds` and the `seed.json` catalog with the poisoned listing. Same tool contract (`docs/implementation-plan.md`, section 5) either way. Checkout always goes to the payment mock, so no real payments happen.
