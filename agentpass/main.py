"""FastAPI app factory for agentpass (token service + gateway + Control API on :8000)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import control, gateway, token_service
from .agent_identity import AgentRegistry, local_agent_signer
from .config import Settings, load_dotenv
from .policy import PolicyEngine
from .risk import ViolationCounter
from .signer import KmsSigner, LocalSigner, Signer
from .storage import DynamoRepo, MemoryRepo, Repo
from .store_client import StoreClient
from .tokens import TokenVerifier
from .users import CognitoUserAuth, LocalUserAuth

log = logging.getLogger("agentpass")


def create_app(
    settings: Settings | None = None,
    *,
    repo: Repo | None = None,
    signer: Signer | None = None,
    user_auth: Any = None,
    store: StoreClient | None = None,
    agent_jwks: dict[str, dict] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.mode == "aws":
        signer = signer or KmsSigner(settings.kms_key_id or "", settings.region)
        repo = repo or DynamoRepo(settings.table_prefix, settings.region)
        user_auth = user_auth or CognitoUserAuth(
            settings.region,
            settings.cognito_user_pool_id or "",
            settings.cognito_client_id or "",
            settings.cognito_client_secret,
        )
    else:
        signer = signer or LocalSigner()
        repo = repo or MemoryRepo()
        user_auth = user_auth or LocalUserAuth(settings.local_users, signer)

    # Registered agents -> public key. The agent proves its identity by signing with the private half.
    if agent_jwks is None:
        if settings.mode == "aws":
            agent_jwks = {settings.agent_id: KmsSigner(settings.agent_kms_key_id or "", settings.region).public_jwk()}
        else:
            agent_jwks = {settings.agent_id: local_agent_signer(settings.agent_key_file).public_jwk()}

    app = FastAPI(title="Aide-commerce", version="0.1.0", docs_url="/docs")
    app.state.settings = settings
    app.state.signer = signer
    app.state.repo = repo
    app.state.user_auth = user_auth
    app.state.agents = AgentRegistry(agent_jwks)
    app.state.verifier = TokenVerifier([signer.public_jwk()], settings.issuer, settings.audience)
    app.state.policy = PolicyEngine.load_default()
    app.state.store = store or StoreClient(settings.store_url)
    app.state.risk = ViolationCounter(settings.auto_revoke_denials, settings.auto_revoke_window_seconds)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(token_service.router, tags=["token-service"])
    app.include_router(gateway.router, tags=["gateway"])
    app.include_router(control.router, tags=["control"])

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {"error": "http_error", "message": str(detail)}
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "mode": settings.mode,
            "signing_key": signer.kid,
            "store_url": app.state.store.url,
            "agent_id": settings.agent_id,
            "agent_key": app.state.agents.kid(settings.agent_id),
        }

    log.info("agentpass mode=%s signer=%s store=%s", settings.mode, signer.kid, app.state.store.url)
    return app


def main() -> None:
    import uvicorn

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
