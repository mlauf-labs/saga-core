"""Bearer-token middleware for the MCP HTTP endpoint (FR-36 / NFR-18)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from docstore.api.auth import verify_bearer_token
from docstore.core.errors import AuthError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Rejects requests without a valid Bearer token (constant-time compare)."""

    def __init__(self, app: object, *, tokens: list[str]) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._tokens = tokens

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else None
        try:
            verify_bearer_token(token, self._tokens)
        except AuthError as exc:
            return JSONResponse(
                status_code=401,
                content={"code": exc.code, "message": str(exc)},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
