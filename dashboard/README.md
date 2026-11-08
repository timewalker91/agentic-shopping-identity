# dashboard/

The parent's **agent security console** (React + Vite + TypeScript). It talks only to the Aide-commerce Control API, never to AWS.

- **Agent Passport:** who the agent is, who it acts for, its mandate and scopes, and a live token countdown. A **REVOKED** stamp lands on it when the session is stopped or auto-revoked.
- **Live decisions:** every tool call (allowed / blocked / revoked) with the reason, polled every second.
- **Budget gauge**, a **Stop agent** button, and a summary of allowed/blocked calls and how much token time was left when the next call was refused.
- Follows the newest session automatically, so the agent CLI can start sessions while the console is open.

```
npm install
npm run dev          # http://127.0.0.1:5173, API at http://127.0.0.1:8000
# other API: VITE_API_URL=http://host:8000 npm run dev
```

Sign in as the parent (local mode: `parent` / `parent-pass`; AWS mode: the Cognito user from `infra/setup.py`).
