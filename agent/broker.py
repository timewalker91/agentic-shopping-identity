"""Credential broker: the only component that ever holds a token.

The model never sees tokens. The shopper calls GatewayClient, which asks the broker for the
Authorization header at send time; the broker refreshes the delegated token through the
token exchange when it is about to expire.

State (id token, sid, current passport) is kept in ~/.agentpass/agent-state.json with mode 0600
so the CLI's login / session / shop / replay commands can run as separate processes.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

DEFAULT_STATE = Path.home() / ".agentpass" / "agent-state.json"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ACTOR_TOKEN_TYPE = "urn:agentpass:params:oauth:token-type:agent-id"
REFRESH_MARGIN_SECONDS = 15


class BrokerError(Exception):
    def __init__(self, reason: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message
        self.status = status


class CredentialBroker:
    def __init__(self, api_url: str, state_path: Path | None = None, agent_id: str = "agent_shopper_v1") -> None:
        self.api_url = api_url.rstrip("/")
        self.agent_id = agent_id
        self._path = state_path or Path(os.environ.get("AGENT_STATE_FILE") or DEFAULT_STATE)
        self._http = httpx.Client(base_url=self.api_url, timeout=20.0)
        self.state: dict[str, Any] = self._load()

    # -- state file -------------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        self._path.write_text(json.dumps(self.state, indent=2))
        os.chmod(self._path, 0o600)

    def clear(self) -> None:
        self.state = {}
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass

    @property
    def sid(self) -> str | None:
        return self.state.get("sid")

    @property
    def mandate_id(self) -> str | None:
        return self.state.get("mandate_id")

    @property
    def username(self) -> str | None:
        return self.state.get("username")

    @property
    def claims(self) -> dict:
        return self.state.get("claims") or {}

    @property
    def seconds_to_expiry(self) -> int:
        return int(self.state.get("expires_at", 0) - time.time())

    # -- http helpers ------------------------------------------------------------------
    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
        except ValueError:
            body = {}
        reason = body.get("reason") or body.get("error") or f"http_{resp.status_code}"
        message = body.get("message") or body.get("error_description") or resp.text[:200]
        raise BrokerError(str(reason), str(message), resp.status_code)

    def _user_headers(self) -> dict[str, str]:
        id_token = self.state.get("id_token")
        if not id_token:
            raise BrokerError("not_logged_in", "run `agent login` first")
        return {"Authorization": f"Bearer {id_token}"}

    # -- flows ---------------------------------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        resp = self._http.post("/auth/login", json={"username": username, "password": password})
        self._raise_for(resp)
        data = resp.json()
        self.state = {
            "api_url": self.api_url,
            "id_token": data["id_token"],
            "user_sub": data["user"]["sub"],
            "username": data["user"]["username"],
            "mandates": [m["mandate_id"] for m in data.get("mandates", [])],
        }
        self._save()
        return data

    def start_session(self, mandate_id: str | None = None) -> dict:
        mandate_id = mandate_id or (self.state.get("mandates") or ["mdt_demo"])[0]
        resp = self._http.post("/sessions", json={"mandate_id": mandate_id}, headers=self._user_headers())
        self._raise_for(resp)
        data = resp.json()
        self.state.update(sid=data["sid"], mandate_id=mandate_id, access_token=None, claims=None, expires_at=0)
        self._save()
        self.exchange()
        return data

    def exchange(self) -> dict:
        """RFC 8693 token exchange: user id token + agent id + session -> delegated token (5 min)."""
        if not self.sid:
            raise BrokerError("no_session", "run `agent session start` first")
        resp = self._http.post(
            "/oauth2/token",
            data={
                "grant_type": GRANT_TYPE,
                "subject_token": self.state.get("id_token", ""),
                "subject_token_type": SUBJECT_TOKEN_TYPE,
                "actor_token": self.agent_id,
                "actor_token_type": ACTOR_TOKEN_TYPE,
                "sid": self.sid,
            },
        )
        self._raise_for(resp)
        data = resp.json()
        claims = data.get("claims") or jwt.decode(data["access_token"], options={"verify_signature": False})
        self.state.update(access_token=data["access_token"], claims=claims, expires_at=claims.get("exp", 0))
        self._save()
        return claims

    def auth_headers(self) -> dict[str, str]:
        """Header for the gateway. Refreshes the passport shortly before it expires."""
        if not self.state.get("access_token") or self.seconds_to_expiry < REFRESH_MARGIN_SECONDS:
            self.exchange()
        return {"Authorization": f"Bearer {self.state['access_token']}"}

    def stop(self) -> dict:
        if not self.sid:
            raise BrokerError("no_session", "no session to stop")
        resp = self._http.post(f"/sessions/{self.sid}/stop", headers=self._user_headers())
        self._raise_for(resp)
        return resp.json()

    def session(self) -> dict:
        if not self.sid:
            raise BrokerError("no_session", "run `agent session start` first")
        resp = self._http.get(f"/sessions/{self.sid}", headers=self._user_headers())
        self._raise_for(resp)
        return resp.json()

    def mandate(self) -> dict:
        if not self.mandate_id:
            raise BrokerError("no_session", "run `agent session start` first")
        resp = self._http.get(f"/mandates/{self.mandate_id}", headers=self._user_headers())
        self._raise_for(resp)
        return resp.json()

    def audit(self, after: str | None = None) -> list[dict]:
        if not self.sid:
            raise BrokerError("no_session", "run `agent session start` first")
        params = {"after": after} if after else {}
        resp = self._http.get(f"/sessions/{self.sid}/audit", params=params, headers=self._user_headers())
        self._raise_for(resp)
        return resp.json().get("events", [])

    def reset_demo(self) -> dict:
        resp = self._http.post("/demo/reset", headers=self._user_headers())
        self._raise_for(resp)
        data = resp.json()
        for key in ("sid", "mandate_id", "access_token", "claims", "expires_at"):
            self.state.pop(key, None)
        self.state["mandates"] = [m["mandate_id"] for m in data.get("mandates", [])]
        self._save()
        return data

    def token_for_replay(self) -> str:
        """The raw delegated token. Only for the explicit `agent token --show` / replay demo."""
        token = self.state.get("access_token")
        if not token:
            raise BrokerError("no_token", "no delegated token saved; run `agent session start`")
        return token
