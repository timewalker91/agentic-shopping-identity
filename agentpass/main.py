"""AgentPass API on :8000.  Run:  uv run uvicorn agentpass.main:app --port 8000"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import control, token_service

app = FastAPI(title="AgentPass", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Isabella's dashboard
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(token_service.router)
app.include_router(control.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
