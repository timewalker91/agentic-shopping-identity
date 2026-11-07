"""The gateway's MCP client to the store server on :8100 (mcp SDK, Streamable HTTP).

The store runs stateless Streamable HTTP, so every call opens a short MCP session. That keeps
the gateway free of connection state and costs a few milliseconds on localhost.
"""
from __future__ import annotations

import json
import re
from typing import Any

from mcp.client.client import Client

_TOOL_ERROR = re.compile(r"^(?:Error (?:executing|calling) tool [\w-]+: )?([a-z_]+): (.*)$", re.S)


class StoreError(Exception):
    """The store rejected the call (unknown sku, empty cart, ...). This is a tool result, not a gateway failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _first_text(content: list[Any]) -> str:
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""


class StoreClient:
    def __init__(self, url: str, timeout: float = 20.0) -> None:
        self._url = url
        self._timeout = timeout
        self._tools: list[dict] | None = None

    @property
    def url(self) -> str:
        return self._url

    async def list_tools(self, refresh: bool = False) -> list[dict]:
        if self._tools is None or refresh:
            async with Client(self._url, read_timeout_seconds=self._timeout) as client:
                result = await client.list_tools()
            self._tools = [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in result.tools]
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        async with Client(self._url, read_timeout_seconds=self._timeout) as client:
            result = await client.call_tool(name, arguments)
        text = _first_text(result.content)
        if result.is_error:
            match = _TOOL_ERROR.match(text.strip())
            if match:
                raise StoreError(match.group(1), match.group(2).strip())
            raise StoreError("store_error", text or "tool failed")
        if isinstance(result.structured_content, dict):
            return result.structured_content
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
