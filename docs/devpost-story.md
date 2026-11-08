## Inspiration
AI agents can now check out on their own, but today they act with the user's own login and card. One poisoned product description can redirect an agent to buy gift cards or ship to an attacker's address, and a stolen token stays valid until it expires. A10's **Agentic Identity** prompt asked how to authenticate an agent, limit which MCP tools it can use, and revoke it mid-task. We built the identity layer we would want before trusting an agent with our own money: a passport for the agent.

## What it does
Aide-commerce gives a shopping agent its own identity and checks it on every tool call:

- **Agent Passport:** the agent proves who it is with a one-time assertion signed by **its own key in AWS KMS**, then exchanges it, together with the parent's sign-in, for a **5-minute token** that names both the parent and the agent (OAuth token exchange, RFC 8693).
- **Only selected tools:** the agent sees 6 shopping tools. Each MCP call is checked against **Cedar** policies (default deny): gift cards, other shipping addresses, other stores and large quantities are blocked, and the "buy gift card" and "change address" tools are hidden entirely.
- **Hard budget:** checkout totals come from the store's own records and are held against the parent's mandate, so the agent can't understate a price or overspend.
- **Revoke mid-task:** the parent's **Stop** button, or 2 policy violations in one session, revokes the agent. Its next call is refused even though its token hasn't expired, a copied token gets **401**, and it can't get a new passport.
- **Your data stays yours:** the agent only ever holds an ID like `addr_home`. The address and card stay with the account owner on the server, so a revoked agent can't make anyone use them.
- **Live dashboard:** the passport, every decision with its reason, the budget, and a big Stop button.

**In the demo video:** a real Claude agent on Amazon Bedrock buys school supplies within a $50 mandate. The parent presses Stop just before the next checkout, which is refused at the gateway: no order, and the address and card are never used. A copied token is refused. Finally, a hijacked agent follows a hidden instruction in a product listing; both of its attacks are blocked and its identity is revoked automatically.

## How we built it
- **Token service, MCP gateway and control API:** Python + FastAPI.
- **Amazon Cognito:** the parent's sign-in.
- **AWS KMS:** two ECC keys, one for the agent's own identity and one for signing passports. Private keys never leave KMS.
- **Amazon DynamoDB:** the revocation list (read with strong consistency on every call), mandates and budget holds, sessions, and the audit log.
- **Amazon Bedrock:** Claude Sonnet 5 as the shopping agent's model.
- **Cedar:** the authorization policies. It's the same language as Amazon Verified Permissions and AgentCore Policy, so our rules port directly to **Amazon Bedrock AgentCore Gateway** in production.
- **Model Context Protocol (MCP):** the standard way the agent calls store tools, so Aide-commerce works with any MCP agent and any MCP store.
- **React + Vite:** the parent's security dashboard.

## Challenges we ran into
- **Stopping an agent instantly.** Agent tokens can be checked without asking a server, so normally they stay valid until they expire. We added a revocation list that the gateway checks on every single call, with no cache, so pressing Stop blocks the agent's very next request. If the list can't be read, the call is blocked.
- **Proving which agent is calling.** Our first version identified the agent by name, which anyone could copy. Now the agent signs a single-use, 60-second proof with its own KMS key; a replayed or forged proof is rejected.
- **Making the budget impossible to beat.** An agent could fire two checkouts at once, or lie about a price. We take the price from the store's records and place a hold on the budget before paying, like a hotel card hold, so two checkouts can never spend the same money.

## Accomplishments that we're proud of
- After **Stop**, the agent's next call is refused in **about 0.7 seconds**, while its token still had **about 295 of its 300 seconds** left.
- A copied token replayed after the revoke gets **HTTP 401**, and a new passport is refused.
- The full demo passed **3 times in a row** on AWS, each run with the real Claude agent.
- **42** automated tests, including the hijacked-agent attack, an impostor agent key and replayed agent proofs.
- **$0** lost to the gift-card attack.

## What we learned
We tested the poisoned listing on real models: **Claude Sonnet 5 recognized the hidden instruction and refused it**, and so did Llama 4 Scout. That's good news, but nobody can promise every model resists every attack. So the hijack in our demo is a simulated compromised agent, and our controls don't depend on the model at all: they judge what the agent **does**. We also learned that agent identity has to name both the human and the agent. Otherwise you can't limit, audit or revoke the agent without locking out the person.

## What's next for Aide-commerce
- Run on **Amazon Bedrock AgentCore Gateway + Policy**, with the same Cedar rules.
- **Owner-managed wallet:** the account owner chooses which addresses and cards each agent may use.
- **Face ID / Touch ID approval** bound to the exact cart for large purchases.
- **Shared security signals (OpenID CAEP)**, so stores and payment providers learn about revocations instantly.
- **Wider revoke:** one agent everywhere, or every agent of one user (the gateway already checks these on every call).
