"""Signed, short lived tokens for every kind of session this server
hands out: verified users, admins, and MCP clients.

JWT (HS256, signed with JWT_SECRET from config.py). Each kind has its own
audience, so an admin token cannot be replayed as an MCP token or the
other way around, and each carries an expiry the server checks on every
use. Tokens are stateless; where revocation matters (MCP clients), the
caller also checks the subject still exists and is not revoked.
"""

import time
import uuid
from typing import Iterable, Optional

import jwt

import config

AUDIENCE_USER = "voice-assistant:user"
AUDIENCE_ADMIN = "voice-assistant:admin"
# MCP tokens are bound to the protected resource they are for, as the MCP
# authorization spec expects (RFC 8707 resource indicators).
AUDIENCE_MCP = f"{config.PUBLIC_BASE_URL}/mcp"

_ALGORITHM = "HS256"


class TokenError(Exception):
    """The token is missing, malformed, expired, or for another audience."""


def issue_token(
    subject: str,
    audience: str,
    ttl_minutes: int,
    scopes: Optional[Iterable[str]] = None,
    extra: Optional[dict] = None,
) -> tuple[str, int]:
    """Returns (token, expires_at as a Unix timestamp)."""
    now = int(time.time())
    expires_at = now + ttl_minutes * 60
    claims = {
        "iss": config.PUBLIC_BASE_URL,
        "sub": subject,
        "aud": audience,
        "iat": now,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    if scopes is not None:
        claims["scope"] = " ".join(scopes)
    if extra:
        claims.update({key: value for key, value in extra.items() if key not in claims})
    return jwt.encode(claims, config.JWT_SECRET, algorithm=_ALGORITHM), expires_at


def decode_token(token: Optional[str], audience: str) -> dict:
    """Verifies signature, expiry, issuer and audience; returns the claims.
    Raises TokenError on any problem, never a raw PyJWT exception."""
    if not token:
        raise TokenError("no token")
    try:
        return jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[_ALGORITHM],
            audience=audience,
            issuer=config.PUBLIC_BASE_URL,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def token_scopes(claims: dict) -> set[str]:
    return set((claims.get("scope") or "").split())
