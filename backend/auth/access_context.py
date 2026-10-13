"""The access level of a request, decided on the server from the session
token only (never from anything the browser says about itself).

A caller is verified when the user_session token is valid, the user still
exists, and the token's amr claim lists "otp" (set by the verification
wizard, backend/verification). A PIN only session, no session, or any
error is a guest, so a failure here can only lower access.

A token whose amr also lists "hub" (the code was checked by the SLT hub) is
only as good as the hub login behind it: the login lives on one open hub
connection. When that connection is gone (expired, closed, evicted, server
restart) the caller is a guest with session_expired set, so the assistant can
ask for a new verification instead of failing in the middle of an answer.
Local mode tokens (no "hub") are unchanged.
"""

from typing import Optional, Tuple

from access import GUEST, AccessContext
from access.context import GUEST_EXPIRED
from auth.dependencies import USER_COOKIE, Connection, _bearer_token
from auth.tokens import AUDIENCE_USER, TokenError, decode_token
from auth.users import get_user


def _hub_session_alive(access: AccessContext) -> bool:
    try:
        import mcp_gateway

        return bool(mcp_gateway.has_session(access))
    except Exception:  # noqa: BLE001  anything unexpected lowers access
        return False


def candidate_from_request(
    conn: Connection, explicit_token: Optional[str] = None, language: Optional[str] = None
) -> Tuple[Optional[AccessContext], bool]:
    """(access built from the first valid token, token lists "hub"), or
    (None, False). No hub check here: logout and /verify/session use it."""
    for token in (explicit_token, _bearer_token(conn), conn.cookies.get(USER_COOKIE)):
        if not token:
            continue
        try:
            claims = decode_token(token, AUDIENCE_USER)
            user = get_user(int(claims["sub"]))
        except (TokenError, KeyError, TypeError, ValueError):
            continue
        except Exception:  # noqa: BLE001  database trouble means guest
            return None, False
        if user is None:
            continue
        amr = claims.get("amr")
        try:
            hub = "hub" in {str(m) for m in (amr or [])}
        except TypeError:
            hub = False
        return AccessContext.from_claims(amr, user.id, user.display_name, user.phone_e164, language), hub
    return None, False


def access_from_request(
    conn: Connection, explicit_token: Optional[str] = None, language: Optional[str] = None
) -> AccessContext:
    """Blocking (one database read); call it on a thread from async code."""
    access, hub = candidate_from_request(conn, explicit_token, language)
    if access is None:
        return GUEST
    if hub and access.is_verified and not _hub_session_alive(access):
        return GUEST_EXPIRED
    return access
