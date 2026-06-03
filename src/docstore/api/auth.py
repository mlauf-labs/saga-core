"""Bearer-token authentication for the REST API (FR-35 / NFR-18)."""

from __future__ import annotations

import secrets

from docstore.core.errors import AuthError


def verify_bearer_token(presented: str | None, valid_tokens: list[str]) -> None:
    """Validate a presented Bearer token using a constant-time comparison.

    Raises:
        AuthError: if the token is missing or does not match any valid token.
    """
    if not presented:
        raise AuthError("Missing Bearer token. Provide 'Authorization: Bearer <token>'.")
    for token in valid_tokens:
        if secrets.compare_digest(presented, token):
            return
    raise AuthError("Invalid Bearer token.")
