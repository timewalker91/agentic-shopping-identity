"""Test harness: a real store MCP server and a real agentpass server, each on a free port.

Both run in local mode (memory tables, local signing key), so the suite needs no AWS. The flows
are the same ones the agent CLI and the dashboard use.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from agentpass.agent_identity import ACTOR_TOKEN_TYPE, local_agent_signer, make_assertion
from agentpass.config import Settings
from agentpass.main import create_app
from store.server import create_app as create_store_app

GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int) -> None:
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def store_url():
    port = free_port()
    server = ServerThread(create_store_app("mock", "127.0.0.1", funds_dollars=150), port)
    server.start()
    yield f"http://127.0.0.1:{port}/mcp"
    server.stop()


@pytest.fixture(scope="session")
def agent_key_file(tmp_path_factory) -> Path:
    """One agent key for the whole run, shared by the server, the harness and CredentialBroker."""
    path = tmp_path_factory.mktemp("agent-key") / "agent_shopper_v1.pem"
    os.environ["AGENTPASS_AGENT_KEY_FILE"] = str(path)
    os.environ.pop("AGENTPASS_AGENT_KMS_KEY_ID", None)
    local_agent_signer(path)  # create it
    return path


@pytest.fixture(scope="session")
def settings(store_url, agent_key_file) -> Settings:
    return Settings(
        mode="local",
        store_url=store_url,
        local_users={"parent": "parent-pass"},
        demo_budget_cents=5000,
        agent_key_file=str(agent_key_file),
    )


@pytest.fixture(scope="session")
def api_url(settings):
    port = free_port()
    server = ServerThread(create_app(settings), port)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


class Harness:
    """The login -> session -> token exchange -> MCP flow, as plain HTTP calls."""

    def __init__(self, http: httpx.Client, agent_key_file: Path | None = None) -> None:
        self.http = http
        self.agent_signer = local_agent_signer(agent_key_file)
        self.id_token = ""
        self.user_headers: dict[str, str] = {}
        self.mandate_id = ""

    def login(self, username: str = "parent", password: str = "parent-pass") -> dict:
        resp = self.http.post("/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        self.id_token = data["id_token"]
        self.user_headers = {"Authorization": f"Bearer {self.id_token}"}
        self.mandate_id = data["mandates"][0]["mandate_id"]
        return data

    def reset(self) -> dict:
        resp = self.http.post("/demo/reset", headers=self.user_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        self.mandate_id = data["mandates"][0]["mandate_id"]
        return data

    def new_session(self) -> str:
        resp = self.http.post("/sessions", json={"mandate_id": self.mandate_id}, headers=self.user_headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["sid"]

    def assertion(self, agent_id: str = "agent_shopper_v1") -> str:
        """A fresh client assertion signed with the agent's key (what the credential broker sends)."""
        return make_assertion(self.agent_signer, agent_id)

    def exchange(self, sid: str, actor_token: str | None = None, actor_token_type: str = ACTOR_TOKEN_TYPE) -> httpx.Response:
        return self.http.post(
            "/oauth2/token",
            data={
                "grant_type": GRANT,
                "subject_token": self.id_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                "actor_token": actor_token if actor_token is not None else self.assertion(),
                "actor_token_type": actor_token_type,
                "sid": sid,
            },
        )

    def passport(self) -> tuple[str, str, dict]:
        sid = self.new_session()
        resp = self.exchange(sid)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        return sid, data["access_token"], data["claims"]

    def rpc(self, token: str | None, method: str, params: dict | None = None) -> httpx.Response:
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.http.post("/mcp", json=body, headers=headers)

    def call(self, token: str, tool: str, args: dict) -> httpx.Response:
        return self.rpc(token, "tools/call", {"name": tool, "arguments": args})

    def stop(self, sid: str) -> dict:
        resp = self.http.post(f"/sessions/{sid}/stop", headers=self.user_headers)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def audit(self, sid: str, after: str | None = None) -> list[dict]:
        params = {"after": after} if after else {}
        resp = self.http.get(f"/sessions/{sid}/audit", params=params, headers=self.user_headers)
        assert resp.status_code == 200, resp.text
        return resp.json()["events"]

    def mandate(self) -> dict:
        resp = self.http.get(f"/mandates/{self.mandate_id}", headers=self.user_headers)
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.fixture
def harness(api_url, agent_key_file):
    with httpx.Client(base_url=api_url, timeout=30) as http:
        h = Harness(http, agent_key_file)
        h.login()
        h.reset()
        yield h


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "agent-state.json"


def error_of(resp: httpx.Response) -> tuple[str, dict]:
    err = resp.json()["error"]
    return err["data"]["reason"], err["data"]


def result_of(resp: httpx.Response) -> dict:
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]["structuredContent"]
