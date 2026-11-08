# Pitch Notes: Aide-commerce (judging, Sun Nov 8)

**Format:** 4-minute demo + 1-minute Q&A, **twice**. All judges are A10 Networks staff (networking and security people), so lead with mechanism, not buzzwords.
**Deck:** the Slides artifact (shared). **Video:** https://youtu.be/EDm-eCqdUHU (1:52). **Full Q&A:** [qa-prep.md](qa-prep.md).

## Roles

| Who | During the 4 minutes | In Q&A |
|---|---|---|
| **Eric** | Speaks the whole pitch and owns the clock. Says "Line 1 / Line 2 / Ending" out loud so the drivers know when to act. | Business, roadmap, "who pays". Hands technical questions over by name. |
| **Jack** | Drives the demo laptop: presses Enter in the Terminal, one step at a time (`scripts/demo.sh`). | Identity, tokens, revocation, gateway internals (Q1–7, 11–13). |
| **Larry** | Stands at the dashboard and clicks **STOP AGENT** when the red line appears, or offers the judge the mouse ("Want to press Stop?"). | Architecture, AWS, MCP, the Bedrock agent (Q8–10, 14–18). |
| **Isabella** | Holds a phone or second laptop with the YouTube video open and paused at 0:00: plan B. | Dashboard and design questions. If she is not there, Larry also holds plan B. |

One answer per question, **under 20 seconds**, then stop. With 1 minute of Q&A, a long answer costs you the next question.

## Before 9:15 (Jack, on the demo laptop)
1. Refresh AWS credentials: `set_aws_creds.sh` in your own Terminal (they expired overnight).
2. Restart the servers so they pick up the new credentials: `scripts/stop-servers.sh && scripts/start-servers.sh`.
3. Warm up: `uv run python -m agent.demo_check --rounds 1 --bedrock` must say PASS.
4. Chrome: http://127.0.0.1:5173, sign in as parent, zoom **80%** so "Live decisions" shows on the right. Hide the Dock.
5. Terminal: `scripts/demo.sh`, stop at the "Press Enter to reset" prompt and wait.
6. **Go / no-go at 9:15:** warm-up passed → live demo. Failed → present with the video (plan B below). Don't debug at the table.

Between round 1 and round 2: Ctrl+C the old demo, run `scripts/demo.sh` again (it signs in and resets by itself). Refresh the dashboard; if it asks, sign in again (logins last 1 hour).

## The 4 minutes

| Time | Slide | Say (short version) |
|---|---|---|
| 0:00–0:15 | Cover | "Your AI assistant can already buy things for you. We built what should exist before you let it: **a passport for the agent**. Its own identity, checked on every tool call, cancelled in under a second." |
| 0:15–0:30 | Problem | "Today the agent shops **as you**: your login, your card. One poisoned product page can tell it to buy gift cards. A leaked token keeps working until it expires, and you can't cut off just the agent." |
| 0:30–0:50 | Passport | "Identity means four answers on every call. **Who:** the agent signs a one-time proof with its own KMS key. **For whom:** the parent is in the token next to the agent. **What:** 6 tools, $50, one store, one address, Cedar default-deny. **Until when:** 5 minutes, or the moment it's revoked." |
| 0:50–1:00 | Architecture | "Every tool call goes through one gateway: passport, revocation list, Cedar, budget, then the store. Cognito, KMS, DynamoDB, Bedrock. The same Cedar rules run on AgentCore Gateway in production." → switch to the dashboard. |
| 1:00–3:15 | **Demo** | See the next section. |
| 3:15–3:30 | Identity | "Is this just a guardrail? No. A guardrail lives inside one agent. Identity travels with the token: the copied token, used from **outside** the agent, was refused. And the agent never held the address or card, only an ID." |
| 3:30–3:40 | Results | Point at both numbers: "**0.7 seconds** from Stop to refused, while the token still had **295 seconds** to live. It was already useless." |
| 3:40–3:55 | Business (skip roadmap) | "Whoever eats the chargeback pays: platforms, payment providers, merchants. A few cents per authorized agent checkout." |
| 3:55–4:00 | Close | "**Your agent can shop. It can't go rogue.** Revoke it, and it can't use your address or card again, because it never had them. Thank you." |

If you're behind at 3:15, skip Business and go straight from Results to Close.

## Live demo (`scripts/demo.sh`, one Enter per step)

| Jack presses Enter → | On screen | Eric says |
|---|---|---|
| (reset) | Dashboard: "Waiting for the agent" | "This is the parent's security console." |
| **Line 1** · session start | Passport card appears: acting for parent, 5:00 countdown | "The agent gets its own passport. Parent and agent are both named. Five minutes." |
| tools | Terminal lists 6 tools | "It only sees six shopping tools. No gift cards, no address changes: those tools don't exist for it." |
| shop (real Claude, ~15 s) | Green "Allowed" rows; checkout $13.48; budget bar moves | "This is a real Claude agent on Amazon Bedrock. Every call is checked: allowed, allowed... it checks out for $13.48, inside the $50 mandate." |
| **Line 2** · session start | New passport | "New task. Now watch what happens when the parent changes their mind." |
| shop, stop scenario | Agent fills the cart. Terminal shows red **>>> PRESS STOP <<<** (10 s) | "It's about to pay with the owner's saved address and card..." → **Larry clicks STOP AGENT** |
| (automatic) | REVOKED stamp; checkout row: "Refused at the gateway: no order placed; your address and card were never used" | "Stopped. The token still has minutes left, and it's already dead. No order, and the agent never touched the address or card." |
| replay | Terminal: HTTP 401 | "We copied the agent's token. Replayed from outside: 401." |
| **Ending** · session start | New passport | "Last one: a compromised agent." |
| hijacked (~10 s) | Red rows: gift card blocked, warehouse address blocked, then REVOKED automatically | "A hidden line in a product listing tells it to buy gift cards and ship them to a warehouse. Blocked, blocked, and two violations revoke its identity automatically. **$0 lost.** Honest note: real Claude refused this injection in our tests, so this agent is simulated. Our controls don't depend on the model." |

**If a step hangs for more than 15 s:** Eric says "Let me show you the recorded run" and switches to plan B at the matching timestamp. Never wait in silence.

## Plan B: narrate the video (1:52, no voice-over)

| Video time | On screen | Say |
|---|---|---|
| 0:00–0:07 | Line 1 title, dashboard waiting | "This is a recording of the same demo from last night." |
| 0:07–0:20 | Passport card, then the 6 tools | Passport line + 6-tools line from the table above. |
| 0:20–0:40 | Real Claude shops, checkout $13.48 | "Real Claude on Bedrock, every call checked, $13.48 inside $50." |
| 0:42–0:58 | Line 2: new passport, cart fills, red "press Stop" | "New task. It's about to pay with the owner's address and card..." |
| 1:00–1:12 | REVOKED stamp, checkout refused | "The parent presses Stop. Refused at the gateway: no order, address and card never used." |
| 1:12–1:20 | Replay → 401 | "Copied token, replayed: 401." |
| 1:20–1:45 | Ending: hijacked agent, two red rows, auto-revoked | "Compromised agent (simulated; real Claude refused this): gift cards blocked, warehouse blocked, revoked automatically." |
| 1:45–1:52 | "Demo complete" | "$0 lost." → back to the deck, Identity slide. |

## 1-minute Q&A: the ten most likely questions

1. **"JWTs are stateless. How is revoke instant?"** (Jack) The gateway reads a revocation list in DynamoDB on **every** call, strongly consistent, no cache. Five minutes only limits exposure; the list makes Stop instant. If the list can't be read, we deny (fail closed).
2. **"How does the agent authenticate?"** (Jack) Two proofs in one OAuth token exchange (RFC 8693): the parent's Cognito login, plus a single-use assertion signed by the agent's own KMS key. The private key never leaves KMS. Out comes a 5-minute passport: `sub` = parent, `act` = agent.
3. **"Can the agent skip the gateway?"** (Larry) The store only accepts calls from the gateway: in the demo it listens on localhost only; in production, an IAM resource policy that allows only the gateway's role. The agent never sees the store's address.
4. **"Isn't this just a guardrail?"** (Eric or Jack) A guardrail lives inside one agent runtime. Our identity travels with the token: a copied token used outside the agent was refused. A guardrail can't do that.
5. **"Did a real model fall for the injection?"** (Larry) No: Claude Sonnet 5 refused it, and so did Llama 4 Scout. That's why the hijack is a simulated agent, and we say so. The gateway judges what the agent **does**, so it works even when a model is fooled.
6. **"Why Cedar?"** (Larry) Default deny, forbid beats permit, readable and analyzable, and it's the same language as AWS Verified Permissions and AgentCore Policy, so the rules port unchanged.
7. **"Latency? Does it scale?"** (Jack) An allowed call is ~420 ms median end to end, mostly DynamoDB round trips from California to Virginia; in the same region, those reads are milliseconds. The gateway is stateless, so you add instances. KMS signing happens once per agent per 5 minutes, not per call.
8. **"Where does this fit in a network stack?"** (Jack) The gateway is a policy enforcement point on the request path, the same place an ADC or API gateway already sits. Verifying the passport and checking the revocation list inline is a natural job for it.
9. **"Can the agent lie about the price or overspend?"** (Jack) No. The total comes from the store's own records, and a conditional write puts a hold on the budget, so two checkouts at once can't spend the same money.
10. **"How much did AI write?"** (whoever is asked) Honest: we used Claude Code. We designed the architecture and contracts, reviewed every module, and wrote 42 tests. Be ready to walk `call_tool` in `agentpass/gateway.py`: token → revocation → mandate → Cedar → budget → forward → audit.

### Product design questions (Isabella or Eric)
- **"Why not a virtual card with a limit?"** A card caps the money, not *what* is bought or *where* it ships: a $50 gift card for an attacker passes a $50 limit. It also can't stop the agent before the order is placed.
- **"What does the user have to do?"** Sign in, set the mandate (budget, store, address), then watch or walk away. Stop is one click; the automatic rule covers them when they aren't watching.
- **"Can they pause and resume?"** No, on purpose: Stop means stop. A new task gets a fresh passport. A pause that keeps old authority alive is what an attacker wants.
- **"Won't strict rules cause false alarms?"** Over budget isn't a violation (the agent picks cheaper items), one denial only blocks that call, and it takes 2 violations to revoke.
- **"Why a passport?"** Everyone knows what it answers: who, vouched for by whom, allowed to do what, until when, and it can be cancelled at the border.

More (Q23–33): [qa-prep.md](qa-prep.md#product-design-isabella-and-eric-lead-larry-for-agent-behavior).

## Do and don't
- **Do** say "simulated" about the hijacked agent, every time. It earns trust.
- **Do** point at the token countdown when it's revoked: time left + refused is the whole point of Prompt 1.
- **Don't** call it "zero trust for everything" or promise it stops all prompt injection. It stops what the injection makes the agent **do**.
- **Don't** read the slides. They are a backdrop; the dashboard is the show.
