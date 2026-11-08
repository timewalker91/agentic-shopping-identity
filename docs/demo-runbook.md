# Demo runbook: Aide-commerce

Everything needed to run the live demo on the demo laptop (Jack's), plus the fallbacks. Checked on 2026-11-07, 20:30: `agent.demo_check` passed 3 of 3 rounds in AWS mode (with the real Bedrock agent) and 3 of 3 in local mode.

**Store for the live demo: the mock store.** It is deterministic, and its prices fit the $50 mandate. The Shopify adapter (`--backend shopify`) uses the same tool contract; show it on a slide, not live (its products cost $600+).

## 1. Morning setup (07:00, about 10 minutes)

1. **Refresh the AWS credentials.** They expire after a few hours. Copy fresh ones from Workshop Studio, run `set_aws_creds.sh` in your own Terminal (never paste keys into a chat), then check:
   ```
   source ~/.aws_workshop_env && aws sts get-caller-identity
   ```
2. **Start the three servers**, one Terminal tab each, from `~/a10hack/agentic-shopping-identity`:
   ```
   source ~/.aws_workshop_env && uv run python -m store.server --backend mock --checkout mock
   source ~/.aws_workshop_env && uv run python -m agentpass
   cd dashboard && npm run dev
   ```
   `curl -s http://127.0.0.1:8000/health` should say `"mode":"aws"`.
3. **Warm-up check** (about 1 minute; it resets the demo when it finishes):
   ```
   source ~/.aws_workshop_env && uv run python -m agent.demo_check --rounds 1 --bedrock
   ```
   It must end with `1/1 rounds passed`. If not, see section 4.
4. **Screen layout:** dashboard (http://127.0.0.1:5173, signed in as `parent`) on the right two-thirds, a large-font Terminal on the left third. Press **Reset demo** on the dashboard.
5. **Log the agent in ahead of time** in the demo Terminal: `source ~/.aws_workshop_env && uv run python -m agent login` (user `parent`, password from `.env`).

## 2. The 4-minute run

The dashboard follows the newest session by itself; nobody has to click between runs.

| Step | Type in the Terminal | What the audience sees |
|---|---|---|
| **A. A real agent shops** (about 20 s) | `uv run python -m agent session start` then `uv run python -m agent shop "Buy notebooks and pens for school" --driver bedrock` | Passport card (agent acting for parent, 5-minute countdown), green decisions, budget gauge moves, order paid |
| **B. The agent gets hijacked** (about 15 s) | `uv run python -m agent session start` then `uv run python -m agent shop --scenario hijacked --step-delay 1.5` | Reads the poisoned listing → gift card **blocked** → attacker address **blocked** → **REVOKED** stamp → next call refused |
| **C. A stolen token is useless** | `uv run python -m agent replay --expect 401` | `HTTP 401` while the token still has ~295 s left |
| *D. Optional: Stop button* | `uv run python -m agent session start` then `uv run python -m agent shop --scenario stop` | Presenter presses **Stop agent** mid-task → "REVOKED by the parent" → next call refused in under a second |

**Say this at step B:** "In our tests, real Claude Sonnet 5 refused this injection. Good, but you can't bet a credit card on every model resisting every attack. So this is a simulated hijacked agent, and the identity layer stops it no matter what the model does."

## 3. Numbers you can quote (measured on this setup)

- Stop → next call refused: **0.7–0.8 s**, with **295–297 s** still left on the token.
- Replay of the copied token after revoke: **HTTP 401**. A new token exchange for that session: **refused** (401 `session_revoked`).
- Passport lifetime: **300 s**. Tools the agent can see: **6** shopper tools (gift-card and change-address tools hidden).
- Real Bedrock agent purchase: about **12–15 s**, $13.48, no denials.

## 4. Fallbacks

| Problem | Do this |
|---|---|
| **Wi-Fi slow or flaky** (the venue eduroam was fine on 11-07; a home network that night failed about half of HTTPS connections, made AWS calls take up to 14 s and crashed `agent session start` once) | Run the warm-up check on the demo network first. If AWS calls are slow, try a phone hotspot; if still slow, use local mode. Decide during the warm-up, not on stage |
| `ExpiredToken` / `Unable to locate credentials` | Refresh the AWS credentials (step 1.1), then restart the API and store tabs |
| Wi-Fi or AWS down, or the workshop account closed | **Local mode** (below). Same demo, no AWS; step A uses the scripted agent instead of Bedrock |
| Dashboard says "Can't reach the Aide-commerce API" | The API tab stopped; restart it |
| Dashboard stuck on "Waiting for the agent" | Run `agent session start`; refresh the page |
| `address already in use` | `lsof -ti tcp:8000 | xargs kill` (or 8100 / 5173), then start again |
| Everything fails | Play the backup video |

**Local mode on the demo laptop** (stop the API and store tabs first):
```
AGENTPASS_MODE=local uv run python -m store.server --backend mock --checkout mock
AGENTPASS_MODE=local uv run python -m agentpass
AGENTPASS_MODE=local uv run python -m agent login        # parent / parent-pass
```
Prefix every `agent` command with `AGENTPASS_MODE=local`. The dashboard login becomes `parent` / `parent-pass` (a public test default that only works in local mode). Check: `AGENTPASS_MODE=local AGENT_PASSWORD=parent-pass uv run python -m agent.demo_check --rounds 1`.

## 5. A teammate's own copy (local mode, no AWS)

```
git pull
mv .env .env.backup 2>/dev/null     # an old .env may point at Shopify or AWS; local mode needs none
uv sync && (cd dashboard && npm install)
AGENTPASS_MODE=local uv run python -m store.server --backend mock --checkout mock
AGENTPASS_MODE=local uv run python -m agentpass
cd dashboard && npm run dev
AGENTPASS_MODE=local uv run python -m agent login        # parent / parent-pass
AGENTPASS_MODE=local uv run python -m agent session start
AGENTPASS_MODE=local uv run python -m agent shop --scenario hijacked --step-delay 1.5
```
Open http://127.0.0.1:5173 on **that** laptop and sign in as `parent` / `parent-pass`. The AWS password does not work here: local mode never contacts Cognito.

## 6. Architecture image

`docs/architecture/aide-commerce-architecture.png` (1920×1080, slide size). Its source is `docs/architecture/diagram.html`. To re-render it after an edit:
```
cd docs/architecture && "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars \
  --window-size=1920,1080 --screenshot="$PWD/aide-commerce-architecture.png" "file://$PWD/diagram.html"
```
