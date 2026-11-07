"""Shared AWS clients. Credentials come from the environment (~/.aws_workshop_env)."""

from __future__ import annotations

from functools import cache

import boto3

from .config import REGION


@cache
def kms():
    return boto3.client("kms", region_name=REGION)


@cache
def ddb():
    return boto3.client("dynamodb", region_name=REGION)


@cache
def table(name: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


@cache
def cognito():
    return boto3.client("cognito-idp", region_name=REGION)
