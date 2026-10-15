"""Optional: ephemeral tokens for a DIRECT browser to Gemini connection.

NOT USED by the default path. The default is the backend proxy (/ws/live),
because every tool call has to run through mcp_gateway on the server. A
browser holding an ephemeral token talks to Gemini directly and its tool calls
would never pass the gateway, the access matrix or the confirmations. If the
owner ever wants that mode, the tool calls would have to be relayed back
through the server first. This helper only documents and tests how a token is
minted (client.auth_tokens.create, v1alpha, docs).

The token is a secret while it lives (one use, a short expiry). It is returned
to the caller and never logged or stored here.
"""

import datetime as _dt
from typing import Any, Optional

from google import genai
from google.genai import types


def mint_ephemeral_token(
    api_key: str,
    model: str,
    live_config: Optional[types.LiveConnectConfig] = None,
    ttl_seconds: int = 120,
    new_session_ttl_seconds: int = 30,
    client: Any = None,
) -> str:
    """One use token, locked to `model` and to `live_config` when given.
    Blocking (one HTTPS call). Raises the SDK's error on failure; the caller
    classifies it with errors.classify."""
    if client is None:
        if not api_key:
            raise ValueError("missing api key")
        client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    constraints = types.LiveConnectConstraints(model=model, config=live_config) if live_config is not None else None
    config = types.CreateAuthTokenConfig(
        uses=1,
        expire_time=now + _dt.timedelta(seconds=max(30, int(ttl_seconds))),
        new_session_expire_time=now + _dt.timedelta(seconds=max(10, int(new_session_ttl_seconds))),
        live_connect_constraints=constraints,
    )
    token = client.auth_tokens.create(config=config)
    return str(token.name)
