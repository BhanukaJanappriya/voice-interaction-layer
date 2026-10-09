"""The access level of a request, decided on the server from the session
token only (never from anything the browser says about itself).

A caller is verified when the user_session token is valid, the user still
exists, and the token's amr claim lists "otp" (set by the verification
wizard, backend/verification). A PIN only session, no session, or any
error is a guest, so a failure here can only lower access.
"""

from typing import Optional

from access import GUEST, AccessContext
from auth.dependencies import USER_COOKIE, Connection, _bearer_token
from auth.tokens import AUDIENCE_USER, TokenError, decode_token
from auth.users import get_user


def access_from_request(
    conn: Connection, explicit_token: Optional[str] = None, language: Optional[str] = None
) -> AccessContext:
    """Blocking (one database read); call it on a thread from async code."""
    for token in (explicit_token, _bearer_token(conn), conn.cookies.get(USER_COOKIE)):
        if not token:
            continue
        try:
            claims = decode_token(token, AUDIENCE_USER)
            user = get_user(int(claims["sub"]))
        except (TokenError, KeyError, TypeError, ValueError):
            continue
        except Exception:  # noqa: BLE001  database trouble means guest
            return GUEST
        if user is None:
            continue
        return AccessContext.from_claims(claims.get("amr"), user.id, user.display_name, user.phone_e164, language)
    return GUEST
