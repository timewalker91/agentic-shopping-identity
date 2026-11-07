"""Create (or find) every AWS resource AgentPass needs. Safe to run again.

What it sets up in us-east-1:
  1. DynamoDB tables for mandates, sessions, revocations, the store and the audit log.
  2. Two KMS signing keys (RSA 2048):
       - alias/agentpass-token-signer     -> the token service signs delegated agent tokens
       - alias/agentpass-agent-shopper-v1 -> the shopping agent signs its client assertion
  3. A Cognito user pool with a web app client and one demo user (the "parent").

It writes every resource ID to .env.local (gitignored). The demo user's password
is generated here and saved only in .env.local; it is never printed.

Run:  uv run python infra/setup.py
"""

from __future__ import annotations

import secrets
import string
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env.local"

# Table name -> (partition key, sort key or None). Matches docs/implementation-plan.md, section 5.
TABLES: dict[str, tuple[str, str | None]] = {
    "ap_mandates": ("mandate_id", None),
    "ap_sessions": ("sid", None),
    "ap_revocations": ("key", None),  # keys like sid#..., agent#..., jti#...
    "ap_store": ("pk", None),  # product#SKU or cart#ID
    "ap_audit": ("sid", "sk"),  # sk = ts#event_id
}

KMS_ALIASES = {
    "TOKEN_SIGNER_KEY": "alias/agentpass-token-signer",
    "AGENT_SHOPPER_V1_KEY": "alias/agentpass-agent-shopper-v1",
}

USER_POOL_NAME = "agentpass-users"
APP_CLIENT_NAME = "agentpass-web"
DEMO_USERNAME = "demo-parent"


def ensure_tables(ddb) -> None:
    existing = set(ddb.list_tables()["TableNames"])
    for name, (pk, sk) in TABLES.items():
        if name in existing:
            print(f"  table {name}: exists")
            continue
        keys = [{"AttributeName": pk, "KeyType": "HASH"}]
        attrs = [{"AttributeName": pk, "AttributeType": "S"}]
        if sk:
            keys.append({"AttributeName": sk, "KeyType": "RANGE"})
            attrs.append({"AttributeName": sk, "AttributeType": "S"})
        ddb.create_table(
            TableName=name,
            KeySchema=keys,
            AttributeDefinitions=attrs,
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"  table {name}: created")
    for name in TABLES:
        ddb.get_waiter("table_exists").wait(TableName=name)
    # jti# entries expire on their own (10 minutes, set by the token service).
    try:
        ddb.update_time_to_live(
            TableName="ap_revocations",
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
    except ClientError as e:
        if "already enabled" not in str(e):
            raise


def ensure_kms_key(kms, alias: str) -> str:
    try:
        return kms.describe_key(KeyId=alias)["KeyMetadata"]["Arn"]
    except kms.exceptions.NotFoundException:
        pass
    key = kms.create_key(
        KeySpec="RSA_2048",
        KeyUsage="SIGN_VERIFY",
        Description=f"AgentPass hackathon signing key ({alias})",
    )["KeyMetadata"]
    kms.create_alias(AliasName=alias, TargetKeyId=key["KeyId"])
    return key["Arn"]


def ensure_user_pool(cognito) -> tuple[str, str, bool]:
    """Return (pool_id, client_id, created_demo_user)."""
    pool_id = None
    for page in cognito.get_paginator("list_user_pools").paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == USER_POOL_NAME:
                pool_id = pool["Id"]
    if pool_id is None:
        pool_id = cognito.create_user_pool(
            PoolName=USER_POOL_NAME,
            Policies={"PasswordPolicy": {"MinimumLength": 12}},
        )["UserPool"]["Id"]
        print(f"  user pool {USER_POOL_NAME}: created")
    else:
        print(f"  user pool {USER_POOL_NAME}: exists")

    client_id = None
    for page in cognito.get_paginator("list_user_pool_clients").paginate(UserPoolId=pool_id, MaxResults=60):
        for client in page["UserPoolClients"]:
            if client["ClientName"] == APP_CLIENT_NAME:
                client_id = client["ClientId"]
    if client_id is None:
        # No client secret: the Control API logs the user in with USER_PASSWORD_AUTH.
        client_id = cognito.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=APP_CLIENT_NAME,
            GenerateSecret=False,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
            AccessTokenValidity=60,
            TokenValidityUnits={"AccessToken": "minutes"},
        )["UserPoolClient"]["ClientId"]

    created = False
    try:
        cognito.admin_get_user(UserPoolId=pool_id, Username=DEMO_USERNAME)
    except cognito.exceptions.UserNotFoundException:
        cognito.admin_create_user(UserPoolId=pool_id, Username=DEMO_USERNAME, MessageAction="SUPPRESS")
        created = True
    return pool_id, client_id, created


def demo_password() -> str:
    # Meets Cognito's default policy: upper, lower, digit, symbol.
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(16))
    return f"Ap{core}9!"


def write_env(values: dict[str, str]) -> None:
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                existing[k] = v
    existing.update(values)
    body = "# Written by infra/setup.py. Gitignored. Resource IDs are not secrets; the password is.\n"
    body += "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n"
    ENV_FILE.write_text(body)
    ENV_FILE.chmod(0o600)


def main() -> None:
    session = boto3.Session(region_name=REGION)
    print("DynamoDB")
    ensure_tables(session.client("dynamodb"))

    print("KMS")
    kms = session.client("kms")
    env: dict[str, str] = {"AWS_REGION": REGION}
    for env_name, alias in KMS_ALIASES.items():
        env[f"{env_name}_ARN"] = ensure_kms_key(kms, alias)
        print(f"  {alias}: ready")

    print("Cognito")
    cognito = session.client("cognito-idp")
    pool_id, client_id, created = ensure_user_pool(cognito)
    env.update({"COGNITO_USER_POOL_ID": pool_id, "COGNITO_CLIENT_ID": client_id, "DEMO_USERNAME": DEMO_USERNAME})
    if created:
        password = demo_password()
        cognito.admin_set_user_password(
            UserPoolId=pool_id, Username=DEMO_USERNAME, Password=password, Permanent=True
        )
        env["DEMO_PASSWORD"] = password
        print(f"  demo user {DEMO_USERNAME}: created (password saved to .env.local)")
    else:
        print(f"  demo user {DEMO_USERNAME}: exists")

    write_env(env)
    print("\nShare these IDs with the team (not secrets):")
    for k in ("COGNITO_USER_POOL_ID", "COGNITO_CLIENT_ID", "TOKEN_SIGNER_KEY_ARN", "AGENT_SHOPPER_V1_KEY_ARN"):
        print(f"  {k}={env[k]}")


if __name__ == "__main__":
    main()
