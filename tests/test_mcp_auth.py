"""Unit tests for the MCP bearer-auth middleware (FR-36)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from docstore.mcp.auth import BearerAuthMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BearerAuthMiddleware, tokens=["secret"])

    @app.get("/mcp")
    async def handler() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_missing_token_rejected() -> None:
    with TestClient(_app()) as client:
        response = client.get("/mcp")
        assert response.status_code == 401
        assert response.json()["code"] == "auth_error"


def test_invalid_token_rejected() -> None:
    with TestClient(_app()) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401


def test_valid_token_allowed() -> None:
    with TestClient(_app()) as client:
        response = client.get("/mcp", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
        assert response.json()["ok"] == "yes"
