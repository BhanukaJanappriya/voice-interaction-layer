"""Who is calling: resolve a verified user or an admin from a request or
a WebSocket handshake.

User sessions arrive three ways, tried in this order, first valid wins:
1. an explicit token (start_session's session_token on /ws/voice),
2. an Authorization: Bearer header (the cross origin development page,
   which cannot rely on cookies),
3. the httpOnly user_session cookie (the normal same origin page; the
   browser also sends it on the WebSocket handshake).
A token only counts if it is valid for the user audience AND the user
still exists in the database, so deleting an account ends its sessions
at once, and the display name always comes from the database, never from
the token or the client.

Admin sessions are cookie only (admin_session, SameSite=Strict), because
the admin portal is always served by this backend itself.
"""

from typing import Optional, Union

from fastapi import HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.websockets import WebSocket

from auth.admins import get_admin
from auth.tokens import AUDIENCE_ADMIN, AUDIENCE_USER, TokenError, decode_token
from auth.users import VerifiedUser, get_user

USER_COOKIE = "user_session"
ADMIN_COOKIE = "admin_session"

Connection = Union[Request, WebSocket]


def client_ip(conn: Connection) -> str:
    """The peer address of the connection. X-Forwarded-For is deliberately
    not trusted: anyone can send it, and trusting it would let one client
    dodge the per address rate limit by inventing addresses. Behind a
    reverse proxy, run uvicorn with --proxy-headers and
    --forwarded-allow-ips set to that proxy, which rewrites conn.client."""
    client = getattr(conn, "client", None)
    return client.host if client and client.host else "unknown"


def _bearer_token(conn: Connection) -> Optional[str]:
    header = conn.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def verified_user_from_token(token: Optional[str]) -> Optional[VerifiedUser]:
    """The user a session token belongs to, or None when the token is
    missing, invalid, expired, for another audience, or the user no longer
    exists. Never raises."""
    if not token:
        return None
    try:
        claims = decode_token(token, AUDIENCE_USER)
        user_id = int(claims["sub"])
    except (TokenError, KeyError, TypeError, ValueError):
        return None
    try:
        return get_user(user_id)
    except SQLAlchemyError as exc:
        print(f"Session check failed, treating the caller as a guest: {exc.__class__.__name__}")
        return None


def verified_user_from_request(conn: Connection, explicit_token: Optional[str] = None) -> Optional[VerifiedUser]:
    """Works for a starlette Request and a WebSocket alike (both expose
    .headers and .cookies). Blocking (one database read)."""
    for token in (explicit_token, _bearer_token(conn), conn.cookies.get(USER_COOKIE)):
        user = verified_user_from_token(token)
        if user is not None:
            return user
    return None


def admin_from_request(conn: Connection) -> Optional[dict]:
    """The admin claims {"sub", "username"} from the admin_session cookie,
    or None. The admin must still exist."""
    try:
        claims = decode_token(conn.cookies.get(ADMIN_COOKIE), AUDIENCE_ADMIN)
        admin_id = int(claims["sub"])
    except (TokenError, KeyError, TypeError, ValueError):
        return None
    try:
        admin = get_admin(admin_id)
    except SQLAlchemyError:
        return None
    if admin is None:
        return None
    return {"sub": str(admin.id), "username": admin.username}


def require_admin(request: Request) -> dict:
    """FastAPI dependency for every admin endpoint:
        @router.get("/x")
        def x(admin: dict = Depends(require_admin)): ...
    Returns {"sub": admin id as text, "username": ...}; raises 401 when
    there is no valid admin session. A plain def, so FastAPI runs its
    database read on the thread pool."""
    admin = admin_from_request(request)
    if admin is None:
        raise HTTPException(status_code=401, detail="Admin sign in required.")
    return admin
