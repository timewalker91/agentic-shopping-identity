"""Create the AWS resources AgentPass needs in us-east-1. Idempotent: safe to run again.

    source ~/.aws_workshop_env
    python -m infra.setup --demo-user parent --demo-password '<a strong password>'

Creates: the five DynamoDB tables (ap_mandates, ap_sessions, ap_revocations, ap_store, ap_audit),
a KMS asymmetric signing key (ECC_NIST_P256, alias/agentpass-token-signing), a Cognito user pool
with an app client (USER_PASSWORD_AUTH, no secret) and the demo user with a permanent password.
Prints the env lines for .env and writes them there (ids only, never credentials).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
TAGS = [{"Key": "project", "Value": "agentpass"}]
TABLES: dict[str, list[tuple[str, str]]] = {
    "mandates": [("mandate_id", "HASH")],
    "sessions": [("sid", "HASH")],
    "revocations": [("key", "HASH")],
    "store": [("pk", "HASH")],
    "audit": [("sid", "HASH"), ("ts_id", "RANGE")],
}
KMS_ALIAS = "alias/agentpass-token-signing"
POOL_NAME = "agentpass-users"
CLIENT_NAME = "agentpass-dashboard"


def ensure_tables(prefix: str) -> list[str]:
    ddb = boto3.client("dynamodb", region_name=REGION)
    created: list[str] = []
    for suffix, keys in TABLES.items():
        name = f"{prefix}{suffix}"
        try:
            ddb.describe_table(TableName=name)
            print(f"  table {name}: exists")
            continue
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        ddb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": attr, "KeyType": kind} for attr, kind in keys],
            AttributeDefinitions=[{"AttributeName": attr, "AttributeType": "S"} for attr, _ in keys],
            BillingMode="PAY_PER_REQUEST",
            Tags=TAGS,
        )
        created.append(name)
        print(f"  table {name}: creating")
    waiter = ddb.get_waiter("table_exists")
    for name in created:
        waiter.wait(TableName=name)
    return created


def ensure_kms_key() -> str:
    kms = boto3.client("kms", region_name=REGION)
    paginator = kms.get_paginator("list_aliases")
    for page in paginator.paginate():
        for alias in page.get("Aliases", []):
            if alias.get("AliasName") == KMS_ALIAS and alias.get("TargetKeyId"):
                print(f"  kms key {KMS_ALIAS}: exists ({alias['TargetKeyId']})")
                return alias["TargetKeyId"]
    key = kms.create_key(
        Description="AgentPass delegated token signing key (ES256)",
        KeyUsage="SIGN_VERIFY",
        KeySpec="ECC_NIST_P256",
        Tags=[{"TagKey": t["Key"], "TagValue": t["Value"]} for t in TAGS],
    )["KeyMetadata"]
    kms.create_alias(AliasName=KMS_ALIAS, TargetKeyId=key["KeyId"])
    print(f"  kms key {KMS_ALIAS}: created ({key['KeyId']})")
    return key["KeyId"]


def ensure_user_pool() -> tuple[str, str]:
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool_id = None
    paginator = idp.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool["Name"] == POOL_NAME:
                pool_id = pool["Id"]
    if pool_id:
        print(f"  cognito pool {POOL_NAME}: exists ({pool_id})")
    else:
        pool_id = idp.create_user_pool(
            PoolName=POOL_NAME,
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 8,
                    "RequireUppercase": False,
                    "RequireLowercase": False,
                    "RequireNumbers": False,
                    "RequireSymbols": False,
                }
            },
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
            UserPoolTags={t["Key"]: t["Value"] for t in TAGS},
        )["UserPool"]["Id"]
        print(f"  cognito pool {POOL_NAME}: created ({pool_id})")

    client_id = None
    paginator = idp.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id, MaxResults=60):
        for client in page.get("UserPoolClients", []):
            if client["ClientName"] == CLIENT_NAME:
                client_id = client["ClientId"]
    if client_id:
        print(f"  cognito app client {CLIENT_NAME}: exists ({client_id})")
    else:
        client_id = idp.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=CLIENT_NAME,
            GenerateSecret=False,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
            PreventUserExistenceErrors="ENABLED",
        )["UserPoolClient"]["ClientId"]
        print(f"  cognito app client {CLIENT_NAME}: created ({client_id})")
    return pool_id, client_id


def ensure_user(pool_id: str, username: str, password: str) -> None:
    idp = boto3.client("cognito-idp", region_name=REGION)
    try:
        idp.admin_get_user(UserPoolId=pool_id, Username=username)
        print(f"  user {username}: exists")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UserNotFoundException":
            raise
        idp.admin_create_user(UserPoolId=pool_id, Username=username, MessageAction="SUPPRESS")
        print(f"  user {username}: created")
    idp.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=password, Permanent=True)
    print(f"  user {username}: permanent password set")


def write_env(path: Path, values: dict[str, str]) -> None:
    lines: list[str] = []
    if path.is_file():
        for line in path.read_text().splitlines():
            key = line.split("=", 1)[0].strip()
            if key not in values:
                lines.append(line)
    lines.extend(f"{k}={v}" for k, v in values.items())
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create AgentPass AWS resources (us-east-1)")
    parser.add_argument("--demo-user", default="parent")
    parser.add_argument("--demo-password", default=os.environ.get("AGENTPASS_DEMO_PASSWORD"))
    parser.add_argument("--table-prefix", default="ap_")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--no-write", action="store_true", help="only print the env lines")
    args = parser.parse_args()
    if not args.demo_password:
        sys.exit("pass --demo-password or set AGENTPASS_DEMO_PASSWORD (never commit it)")
    if not (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE") or os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")):
        print("warning: no AWS credentials in the environment; `source ~/.aws_workshop_env` first", file=sys.stderr)

    print("DynamoDB")
    ensure_tables(args.table_prefix)
    print("KMS")
    key_id = ensure_kms_key()
    print("Cognito")
    pool_id, client_id = ensure_user_pool()
    ensure_user(pool_id, args.demo_user, args.demo_password)

    values = {
        "AGENTPASS_MODE": "aws",
        "AWS_REGION": REGION,
        "AGENTPASS_TABLE_PREFIX": args.table_prefix,
        "AGENTPASS_KMS_KEY_ID": key_id,
        "COGNITO_USER_POOL_ID": pool_id,
        "COGNITO_CLIENT_ID": client_id,
    }
    print("\nenv:")
    for key, value in values.items():
        print(f"{key}={value}")
    if not args.no_write:
        write_env(Path(args.env_file), values)
        print(f"\nwritten to {args.env_file} (gitignored). Login: agent login --user {args.demo_user}")


if __name__ == "__main__":
    main()
