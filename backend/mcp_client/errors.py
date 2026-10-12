"""Typed errors of the MCP hub client.

Callers (tools.py, the OTP customer lookup, /health) decide what to do
from the TYPE, never from message text: an auth error must never be
retried, a network error may be, a tool error is the hub working fine
but refusing one request, and an open breaker means "do not even try".

Messages are short and safe to log or show on /health: they never contain
the API key (client.py scrubs it before building them).
"""


class HubError(Exception):
    """Base class of everything the hub client raises on purpose.

    reason is a short machine readable failure class, safe to log and to
    show on /health: not_configured, dns, tls, connect, timeout, budget,
    http_status, no_transport, auth_rejected, tool_error, breaker_open.
    Callers may branch on it; the exception TYPE stays the main contract."""

    default_reason = "error"

    def __init__(self, *args, reason: str = "", status: "int | None" = None):
        super().__init__(*args)
        self.reason = reason or self.default_reason
        self.status = status  # the HTTP status behind it, when there was one


class HubUnavailable(HubError):
    """The hub could not be reached or did not answer in time: not
    configured, connection refused, DNS failure, timeout, HTTP 5xx, no
    transport spoke MCP. The only error class that is retried."""

    default_reason = "unavailable"


class HubAuthError(HubError):
    """The hub rejected the credentials (HTTP 401 or 403). Never retried:
    the same key will be rejected again, and hammering a hub that has
    just said no only gets the key blocked."""

    default_reason = "auth_rejected"


class HubToolError(HubError):
    """The hub answered, but the tool call itself failed (unknown tool,
    invalid arguments, or a result flagged isError). Not a connectivity
    problem, so it neither retries nor trips the breaker."""

    default_reason = "tool_error"


class HubBreakerOpen(HubError):
    """A recent failure put the hub on pause (MCP_HUB_BREAKER_SECONDS);
    no network call was made. retry_in is the seconds left."""

    default_reason = "breaker_open"

    def __init__(self, retry_in: float):
        super().__init__(f"hub paused after a recent failure, retry in {int(retry_in) + 1}s")
        self.retry_in = retry_in
