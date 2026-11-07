# agent/

**Owner:** Larry. Shopping agent on Amazon Bedrock + credential broker. The broker signs a client assertion with the agent's KMS key, exchanges it at `POST :8000/oauth2/token` (see `docs/implementation-plan.md`, section 5), attaches the delegated token to MCP calls to `:8000/mcp`, and stops the task on 401. The model never sees tokens.
