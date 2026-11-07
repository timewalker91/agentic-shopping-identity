"""Settings, read from .env.local (written by infra/setup.py)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env.local")

REGION = os.environ.get("AWS_REGION", "us-east-1")
BASE_URL = os.environ.get("AGENTPASS_BASE_URL", "http://localhost:8000")
STORE_MCP_URL = os.environ.get("STORE_MCP_URL", "http://127.0.0.1:8100/mcp")  # Larry's store; only the gateway calls it

# Delegated agent token (docs/implementation-plan.md, section 5)
ISSUER = "agentpass"
AUDIENCE = "agentpass-gateway"
TOKEN_ENDPOINT = f"{BASE_URL}/oauth2/token"
DELEGATED_TOKEN_TTL = 300  # 5 minutes
ASSERTION_MAX_TTL = 60  # agent client assertions live at most 60 s

COGNITO_USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
TOKEN_SIGNER_KEY_ARN = os.environ["TOKEN_SIGNER_KEY_ARN"]

# Registered agents. Each has its own KMS key and a fixed tool profile.
AGENTS: dict[str, dict] = {
    "agent_shopper_v1": {
        "key_arn": os.environ["AGENT_SHOPPER_V1_KEY_ARN"],
        "tool_profile": "shopper_v1",
        "scopes": {"catalog:read", "cart:write", "checkout:execute"},
    },
}

T_MANDATES = "ap_mandates"
T_SESSIONS = "ap_sessions"
T_REVOCATIONS = "ap_revocations"
T_STORE = "ap_store"
T_AUDIT = "ap_audit"
