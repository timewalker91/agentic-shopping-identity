# Judge Q&A Prep: Aide-commerce Security Core

Short answers, grounded in our code. Practice saying each answer in under 30 seconds. "**Show:**" names the file to open if a judge wants to see it.

## Numbers to remember
Measured in **AWS mode** (KMS, Cognito, DynamoDB) on the demo laptop, Santa Clara → us-east-1, with `uv run python -m agentpass.measure`:

| What | Result |
|---|---|
| **Stop pressed → agent's next call refused** | **~0.7 s**, while the token still had **294 of 300 s** left |
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
The gateway checks a revocation list in DynamoDB on **every** tool call, with a strongly consistent read. The 5-minute lifetime only limits exposure; the list is what makes Stop instant. Measured: refused within ~0.4 s while the token still had 296 s left.
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

**10. Why revoke the whole session after 2 denials, instead of just blocking those calls?**
A denied gift-card purchase is evidence the agent is hijacked. A hijacked agent will keep trying things that *are* allowed, such as spending the rest of the budget on the attacker's product. Revoking the identity stops everything it would do next. We use 2 denials, not 1, so an honest mistake doesn't kill the task.

**11. Why doesn't over-budget count as a violation?**
Going over budget is usually an honest mistake, not an attack. The agent gets `over_budget` with the remaining amount and can pick cheaper items.

**12. Can the agent lie about the price?**
No. The budget check uses the cart total computed by the store and read from DynamoDB, never a number the agent sends. A conditional write places a hold, so two checkouts at once can't overspend.
**Show:** `agentpass/gateway.py` `_checkout` and `agentpass/storage.py` `try_hold`.

**13. Can the agent skip the gateway and call the store directly?**
In the demo, the store listens only on localhost, and only the gateway knows its address. In production, the store accepts calls only from the gateway's identity, for example an IAM resource policy that allows only the gateway role.

## AWS & architecture

**14. What AWS services do you use?**
- **Cognito:** the human's login.
- **KMS:** agent keys and token signing; private keys never leave KMS.
- **DynamoDB:** revocation list, mandates and budget ledger, sessions, audit log.
- **Bedrock:** the shopping agent's model.

Our design maps one-to-one onto **AgentCore Gateway + Policy** for production, with the same Cedar rules.

**15. Why didn't you use AgentCore Gateway directly?**
With about 20 hours, we wanted full control and something we can explain line by line. The token service, Cedar policies and revocation logic are portable: in production they sit behind AgentCore Gateway as a policy engine plus a request interceptor.

**16. Does it scale?**
- The gateway is stateless, so you add instances.
- DynamoDB is on-demand.
- Token signing happens once per agent per 5 minutes, not per call.
- Each call is verified locally with the cached public key.

**17. Why MCP?**
MCP is the standard way agents call tools. Because we enforce at the protocol layer, Aide-commerce works with any MCP agent and any MCP tool server without changing them.

## About the build

**18. How much did AI write?**
Be honest. We used Claude Code to write code. We designed the architecture and the contracts, reviewed every module, and wrote tests that prove each rule. Be ready to walk through `call_tool` in `gateway.py` in order: token → revocation → mandate → Cedar → budget → forward → audit.
