"""Timestamps used everywhere: UTC ISO 8601 with microseconds, e.g. 2026-11-07T18:04:05.123456Z."""
from __future__ import annotations

import time
from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_epoch() -> int:
    return int(time.time())
