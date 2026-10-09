"""Browser facing LiveKit endpoints: a join token and the transport config.

POST /api/livekit/token mints a short lived LiveKit access token for ONE
conversation: a random room name per call, an identity that says who the
caller is (`user-<id>` for a verified account, `guest-<random>` for a Guest),
and grants that only allow joining that room, publishing the microphone and
subscribing to the assistant. The token also asks LiveKit to dispatch the
voice agent worker (config.LIVEKIT_AGENT_NAME) into the new room, so no
separate "start the agent" call exists and a browser cannot choose which
agent it gets.

Who the caller is comes from the same place /ws/voice and /chat use
(auth.dependencies.verified_user_from_request: session_token in the body,
Authorization bearer, or the user_session cookie). For a verified user the
display name in the token is the one on the account; the body's user_name is
only used for Guests, exactly as on the WebSocket path. Because the
language, voice style and name travel as participant attributes inside the
signed token, the browser cannot change them afterwards (it is not granted
can_update_own_metadata), so the agent can trust them.

GET /api/livekit/config tells the page which transport to use and whether
LiveKit can be used at all. It exposes no secret.

Nothing here logs the API secret or a token; the only thing printed is the
room name and the kind of identity, which carry no credential.
"""

import secrets
import time
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import config
from auth.access_context import access_from_request
from auth.dependencies import verified_user_from_request

router = APIRouter(prefix="/api/livekit", tags=["livekit"])

# Set by the app (server.py) to a callable returning the worker's status dict,
# or None when this process runs no worker. Without it the agent is assumed to
# run elsewhere (for example the standalone worker command) and is not checked.
_agent_status_provider = None


def set_agent_status_provider(provider) -> None:
    global _agent_status_provider
    _agent_status_provider = provider


def agent_ready() -> bool:
    """True when the agent is running, or when this process does not run it
    (so there is nothing here to check). Never raises."""
    provider = _agent_status_provider
    if provider is None:
        return True
    try:
        status = provider()
    except Exception:
        return False
    if status is None:
        return True
    return status.get("state") == "running"

VOICE_STYLES = ("female_natural", "male")
DEFAULT_VOICE_STYLE = "female_natural"
LANGUAGES = ("en", "si", "ta")
MAX_NAME_CHARS = 40


class TokenRequest(BaseModel):
    """Every field is optional: an empty body is a Guest with the default
    language (auto detect) and voice."""

    language: Optional[str] = Field(default=None, max_length=8)
    voice_style: Optional[str] = Field(default=None, max_length=32)
    user_name: Optional[str] = Field(default=None, max_length=200)
    session_token: Optional[str] = Field(default=None, max_length=4096)


class TokenResponse(BaseModel):
    url: str
    token: str
    room: str
    identity: str
    expires_at: int  # Unix timestamp (seconds), like /auth/verify


class TransportConfig(BaseModel):
    transport: str
    livekit_configured: bool
    avatar_renderer: str
    # False while the voice agent is not running (still starting, retrying
    # because LiveKit is unreachable, or stopped). The page then uses
    # /ws/voice and the local pipeline instead of joining an empty room.
    agent_ready: bool = True


def normalise_voice_style(value: Optional[str]) -> str:
    """Only the two supported styles are accepted; anything else (including
    a missing value) becomes the default instead of an error, so an old
    client never gets locked out."""
    return value if value in VOICE_STYLES else DEFAULT_VOICE_STYLE


def normalise_language(value: Optional[str]) -> str:
    """"" means auto detect, same as language_hint null on /ws/voice."""
    return value if value in LANGUAGES else ""


def clean_name(value: Optional[str]) -> str:
    return " ".join((value or "").split())[:MAX_NAME_CHARS]


def build_token(
    *,
    identity: str,
    room: str,
    attributes: dict,
    display_name: str = "",
    ttl_seconds: Optional[int] = None,
) -> str:
    """Builds the signed JWT. Split from the endpoint so tests (and the
    worker's own smoke checks) can build one without an HTTP call."""
    from livekit import api

    ttl = int(ttl_seconds if ttl_seconds is not None else config.LIVEKIT_TOKEN_TTL_SECONDS)
    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_ttl(timedelta(seconds=ttl))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                # The page sends mute and voice changes on a data topic.
                can_publish_data=True,
                # Microphone only: no camera, screen share or other source.
                can_publish_sources=["microphone"],
            )
        )
        .with_attributes({key: str(value) for key, value in attributes.items()})
        .with_room_config(
            api.RoomConfiguration(
                # The room disappears soon after the person leaves.
                empty_timeout=30,
                departure_timeout=10,
                agents=[api.RoomAgentDispatch(agent_name=config.LIVEKIT_AGENT_NAME)],
            )
        )
    )
    if display_name:
        token = token.with_name(display_name)
    return token.to_jwt()


@router.get("/config", response_model=TransportConfig)
def livekit_config() -> TransportConfig:
    """What the page needs to pick a transport. No secret, no URL."""
    return TransportConfig(
        transport=config.VOICE_TRANSPORT,
        livekit_configured=config.livekit_configured(),
        avatar_renderer=config.AVATAR_RENDERER,
        agent_ready=agent_ready(),
    )


@router.post("/token", response_model=TokenResponse)
def create_token(body: TokenRequest, request: Request) -> TokenResponse:
    """A plain def, so FastAPI runs it (and the one database read inside
    verified_user_from_request) on its thread pool."""
    if not config.livekit_configured():
        raise HTTPException(
            status_code=503,
            detail="LiveKit is not configured on this server (set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET).",
        )

    if not agent_ready():
        # A room with no agent in it would be silent. Refusing here lets the
        # page fall back to /ws/voice and the local pipeline straight away.
        raise HTTPException(status_code=503, detail="The LiveKit voice agent is not running right now.")

    verified = verified_user_from_request(request, body.session_token)
    if verified is not None:
        identity = f"user-{verified.id}"
        display_name = verified.display_name
    else:
        identity = f"guest-{secrets.token_hex(6)}"
        display_name = clean_name(body.user_name)

    room = f"va-{secrets.token_urlsafe(12)}"
    # Decided here from the session token (OTP verified or not) and signed into
    # the token, so the agent trusts it and the browser cannot raise it.
    access_level = "verified" if access_from_request(request, body.session_token).is_verified else "guest"
    attributes = {
        "language": normalise_language(body.language),
        "voice_style": normalise_voice_style(body.voice_style),
        "user_name": display_name,
        # The agent skips "my name is" renaming for a verified account.
        "verified": "true" if verified is not None else "false",
        "access": access_level,
    }
    jwt_token = build_token(identity=identity, room=room, attributes=attributes, display_name=display_name)
    expires_at = int(time.time()) + int(config.LIVEKIT_TOKEN_TTL_SECONDS)
    print(f"LiveKit token issued: room {room}, {'verified user' if verified else 'guest'}.")
    return TokenResponse(url=config.LIVEKIT_URL, token=jwt_token, room=room, identity=identity, expires_at=expires_at)
