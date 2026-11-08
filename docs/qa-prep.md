# Judge Q&A Prep: Aide-commerce Security Core

Short answers, grounded in our code. Practice saying each answer in under 30 seconds. "**Show:**" names the file to open if a judge wants to see it.

## Numbers to remember
Measured in **AWS mode** (KMS, Cognito, DynamoDB) on the demo laptop, Santa Clara → us-east-1, with `uv run python -m agentpass.measure`:

| What | Result |
|---|---|
| **Stop pressed → agent's next call refused** | **~0.7 s**, while the token still had **about 295 of 300 s** left |
| Full demo check (`uv run python -m agent.demo_check --bedrock`) | **3 of 3** rounds passed in AWS mode, each with the real Bedrock agent; Stop → refused in 0.7–0.8 s with 295–297 s left |
| Real Bedrock agent, normal purchase | about 12–15 s end to end, no denials |
| Allowed tool call, agent → gateway → store and back | ~420 ms median; most of it is DynamoDB round trips from California to Virginia |
| Token exchange (agent signs its assertion in KMS, we sign the passport in KMS) | ~1 s, once per 5 minutes per agent, not per call |
| Delegated token lifetime | 5 minutes |
| Auto-revoke threshold | 2 policy denials in one session |

## Identity

**1. How does the agent authenticate?**
Two proofs, combined in one token exchange (RFC 8693):
- The human's Cognito login token.
- A one-time assertion signed by **the agent's own KMS key**, so the private key never leaves KMS.

If both check out, we issue a 5-minute token that names the user (`sub`), the agent (`act`), the session and the mandate.
**Show:** `agentpass/agent_identity.py` (`AgentRegistry.verify`) and `agentpass/token_service.py` (`token_exchange`).

**2. Why not just give the agent the user's token?**
Then every action looks like the human did it. We couldn't revoke the agent without logging the human out, couldn't limit it to shopping tools, and couldn't tell in the audit log who acted. Our token carries both identities.

**3. Can someone replay the agent's signed assertion?**
No. Each assertion has a unique ID (`jti`) that we store for 10 minutes. A second use is rejected; the smoke test checks this.

**4. What if the agent's token is stolen?**
It only works against our gateway (audience), only for 5 minutes, and we can revoke it on the very next call by session, agent, user or token ID. Binding the token to the client (DPoP) is on the roadmap.

## Revocation (the core of Prompt 1)

**5. JWTs are stateless. How is revocation immediate?**
The gateway checks a revocation list in DynamoDB on **every** tool call, with a strongly consistent read. The 5-minute lifetime only limits exposure; the list is what makes Stop instant. Measured: refused within ~0.7 s while the token still had about 295 s left.
**Show:** `agentpass/gateway.py`, `_check_revocation`.

**6. What if DynamoDB is down?**
We **fail closed**: if the revocation list can't be read, the call is denied.

**7. What can be revoked?**
A session (Stop button, or automatically), an agent, a user, a mandate, or a single token. The token service refuses new tokens for anything revoked, so the agent can't just get a fresh one.

## Authorization & prompt injection

**8. Why Cedar policies instead of if-statements?**
- Cedar denies anything not explicitly permitted, and a forbid always beats a permit.
- The rules are readable and can be analyzed, which code can't.
- It's the same policy language AWS uses in Verified Permissions and AgentCore Policy, so our rules port to them unchanged.

**Show:** `agentpass/policies/shopper.cedar`. It's 5 rules.

**9. How do you stop prompt injection? Do you detect it with an LLM?**
We don't need to know whether the model was fooled. The gateway enforces rules deterministically on what the agent **does**: gift-card SKUs are forbidden, checkout must ship to the address bound in the token, and dangerous tools are hidden from the tool list. A classifier like Bedrock Guardrails is an extra layer on our roadmap, not the main defense: classifiers are probabilistic, our rules aren't.

**10. Is the agent real? Did a real model actually fall for the injection?**
The agent is real: Claude Sonnet 5 on Amazon Bedrock, calling our gateway over MCP, and it does the normal purchase live. We tested it on the poisoned listing tonight. **Claude Sonnet 5 spotted the hidden instruction and refused it**, even when the goal told it to follow the listing; Llama 4 Scout ignored it too. That's good news, but nobody can promise every model resists every injection, so the hijack in the demo is a **simulated compromised agent**, and we say so. The point is that our controls don't depend on the model: the gateway judges what the agent *does*.
**Show:** `agent/scripted.py` (the simulated hijack) and `agent/shopper.py` (the real Bedrock agent).

**11. Why revoke the whole session after 2 denials, instead of just blocking those calls?**
A denied gift-card purchase is evidence the agent is hijacked. A hijacked agent will keep trying things that *are* allowed, such as spending the rest of the budget on the attacker's product. Revoking the identity stops everything it would do next. We use 2 denials, not 1, so an honest mistake doesn't kill the task.

**12. Why doesn't over-budget count as a violation?**
Going over budget is usually an honest mistake, not an attack. The agent gets `over_budget` with the remaining amount and can pick cheaper items.

**13. Can the agent lie about the price?**
No. The budget check uses the cart total computed by the store and read from DynamoDB, never a number the agent sends. A conditional write places a hold, so two checkouts at once can't overspend.
**Show:** `agentpass/gateway.py` `_checkout` and `agentpass/storage.py` `try_hold`.

**14. Can the agent skip the gateway and call the store directly?**
In the demo, the store listens only on localhost, and only the gateway knows its address. In production, the store accepts calls only from the gateway's identity, for example an IAM resource policy that allows only the gateway role.

## AWS & architecture

**15. What AWS services do you use?**
- **Cognito:** the human's login.
- **KMS:** agent keys and token signing; private keys never leave KMS.
- **DynamoDB:** revocation list, mandates and budget ledger, sessions, audit log.
- **Bedrock:** the shopping agent's model.

Our design maps one-to-one onto **AgentCore Gateway + Policy** for production, with the same Cedar rules.

**16. Why didn't you use AgentCore Gateway directly?**
With about 20 hours, we wanted full control and something we can explain line by line. The token service, Cedar policies and revocation logic are portable: in production they sit behind AgentCore Gateway as a policy engine plus a request interceptor.

**17. Does it scale?**
- The gateway is stateless, so you add instances.
- DynamoDB is on-demand.
- Token signing happens once per agent per 5 minutes, not per call.
- Each call is verified locally with the cached public key.

**18. Why MCP?**
MCP is the standard way agents call tools. Because we enforce at the protocol layer, Aide-commerce works with any MCP agent and any MCP tool server without changing them.

## About the build

**19. How much did AI write?**
Be honest. We used Claude Code to write code. We designed the architecture and the contracts, reviewed every module, and wrote tests that prove each rule. Be ready to walk through `call_tool` in `gateway.py` in order: token → revocation → mandate → Cedar → budget → forward → audit.

## Likely from A10 judges

**20. Isn't this just a guardrail?**
A guardrail lives inside one agent's runtime. Our identity travels with the token: in the demo, a copied token replayed from **outside** the agent was refused (401). A guardrail inside the agent can't stop that; revoking the identity can. And the agent never held the address or the card, only an ID (`addr_home`).

**21. Where does this fit in a network stack?**
The gateway is a policy enforcement point on the request path, the same place an ADC or API gateway already sits. Verifying the passport signature and checking the revocation list inline is a natural job there; the Cedar rules and the revocation list stay the same.

**22. What happens to the user's personal data after a revoke?**
The agent never had it. The token carries only `ship_to_id=addr_home`; the address book and the payment method stay on the server with the account owner. After a revoke, every call is refused at the gateway, so the agent can't make anyone use them.

## Product design (Isabella and Eric lead; Larry for agent behavior)

**23. Who is the user, and why a parent?**
Anyone who hands spending to someone else with limits. A parent letting an agent buy school supplies is the easiest case to picture: a budget, one store, one home address, and a clear "stop" moment. The same pattern fits company procurement agents on corporate cards, which is the business case.

**24. What does the user actually have to do?**
Sign in once and set the mandate (budget, store, address). Then they can watch or walk away. The dashboard shows each decision in plain words, and Stop is one click. If they aren't watching, the automatic rule (2 violations) still cuts the agent off.

**25. Why not just give the agent a virtual card with a $50 limit?**
A card limit caps the money, not *what* is bought, *where* it ships, or *which* actions the agent can take. A $50 gift card for an attacker passes a $50 card limit. A card also can't stop the agent before the order is placed, and it doesn't say which agent is buying. Virtual cards are on our roadmap as an extra layer, not a replacement.

**26. Why the passport metaphor?**
Everyone already knows what a passport answers: who you are, who vouches for you, what you're allowed to do, when it expires, and that it can be cancelled at the border. That's exactly what the agent's token carries, so the dashboard shows it as a passport card with a countdown and a REVOKED stamp.

**27. Can the user pause and resume the agent after Stop?**
No, on purpose: Stop means stop. That session's passport is dead, and the agent can't get a new one for it. To continue, the user starts a new task, which issues a fresh passport. A "pause" that keeps old authority alive is exactly what an attacker would want.

**28. What does the user see when something is blocked?**
A plain-language line in the live feed, not an error code: for example, "Refused at the gateway: no order placed; your address and card were never used", or that a gift card was blocked. The stats show calls allowed, calls blocked, money spent, and "$0 lost" after an attack.

**29. Won't strict rules annoy users with false alarms?**
That's the main trade-off we designed for. Over budget is not a violation: the agent is told how much is left and picks cheaper items. One denied call only blocks that call; it takes 2 violations to revoke. Default deny means a new kind of purchase needs a rule change, which we accept for money.

**30. Why 5 minutes? Won't the agent keep getting logged out?**
The user never sees it. The agent's broker quietly gets a fresh passport before the old one expires, as long as the session isn't revoked. Five minutes only limits how long a leaked token is useful; Stop works instantly regardless.

**31. How would the user get alerts when they're not looking at the dashboard?**
Not built yet. Today the dashboard refreshes every second. Next: push a notification to the phone on a revoke or a blocked attack, and Face ID approval for big purchases, bound to the exact cart.

**32. Does it work with real stores?**
Any store that exposes its tools over MCP. Larry built an adapter for a Shopify development store behind the same tool contract; the demo uses our mock store so it's predictable on stage. The store doesn't need to change: the gateway sits in front of it.

**33. What about several agents, or a family with several kids?**
Each agent has its own key and its own passport, so each gets its own mandate and can be stopped alone. The gateway already checks revocations by session, agent, mandate and user, so "stop this one agent everywhere" or "stop every agent of this account" are one write each. The screens for that are on the roadmap.

