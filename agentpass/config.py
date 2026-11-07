"""Settings for the agentpass process. Everything comes from environment variables.

Two modes:
- local: no AWS at all. In-memory tables, a local EC P-256 signing key, local demo users.
- aws:   DynamoDB tables, KMS signing, Cognito login. Needs the ids printed by infra/setup.py.

AWS credentials are never read from files by this code; boto3 picks them up from the
environment (`source ~/.aws_workshop_env`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ without overriding what is already set."""
    path = path or REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)


def _parse_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" in pair:
            name, _, password = pair.partition(":")
            users[name.strip()] = password.strip()
    return users


@dataclass(frozen=True)
class Settings:
    mode: str = "local"
    region: str = "us-east-1"
    host: str = "127.0.0.1"
    port: int = 8000
    store_url: str = "http://127.0.0.1:8100/mcp"
    issuer: str = "agentpass"
    audience: str = "agentpass-gateway"
    token_ttl_seconds: int = 300
    agent_id: str = "agent_shopper_v1"
    tool_profile: str = "shopper_v1"
    kms_key_id: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_client_secret: str | None = None
    table_prefix: str = "ap_"
    auto_revoke_denials: int = 2
    auto_revoke_window_seconds: int = 300
    local_users: dict[str, str] = field(default_factory=lambda: {"parent": "parent-pass"})
    demo_budget_cents: int = 5000
    demo_merchant: str = "freshmart"
    demo_ship_to_id: str = "addr_home"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        kms = env.get("AGENTPASS_KMS_KEY_ID") or None
        pool = env.get("COGNITO_USER_POOL_ID") or None
        client = env.get("COGNITO_CLIENT_ID") or None
        mode = env.get("AGENTPASS_MODE") or ("aws" if (kms and pool and client) else "local")
        if mode not in ("local", "aws"):
            raise ValueError(f"AGENTPASS_MODE must be 'local' or 'aws', got {mode!r}")
        if mode == "aws" and not (kms and pool and client):
            raise ValueError(
                "AGENTPASS_MODE=aws needs AGENTPASS_KMS_KEY_ID, COGNITO_USER_POOL_ID and "
                "COGNITO_CLIENT_ID (run `python -m infra.setup` and copy its output to .env)"
            )
        budget = float(env.get("AGENTPASS_DEMO_BUDGET", "50"))
        return cls(
            mode=mode,
            region=env.get("AWS_REGION", "us-east-1"),
            host=env.get("AGENTPASS_HOST", "127.0.0.1"),
            port=int(env.get("AGENTPASS_PORT", "8000")),
            store_url=env.get("AGENTPASS_STORE_URL", "http://127.0.0.1:8100/mcp"),
            token_ttl_seconds=int(env.get("AGENTPASS_TOKEN_TTL", "300")),
            kms_key_id=kms,
            cognito_user_pool_id=pool,
            cognito_client_id=client,
            cognito_client_secret=env.get("COGNITO_CLIENT_SECRET") or None,
            table_prefix=env.get("AGENTPASS_TABLE_PREFIX", "ap_"),
            auto_revoke_denials=int(env.get("AGENTPASS_AUTO_REVOKE_DENIALS", "2")),
            auto_revoke_window_seconds=int(env.get("AGENTPASS_AUTO_REVOKE_WINDOW", "300")),
            local_users=_parse_users(env.get("AGENTPASS_LOCAL_USERS", "parent:parent-pass")),
            demo_budget_cents=int(round(budget * 100)),
            demo_merchant=env.get("AGENTPASS_DEMO_MERCHANT", "freshmart"),
            demo_ship_to_id=env.get("AGENTPASS_DEMO_SHIP_TO", "addr_home"),
        )
