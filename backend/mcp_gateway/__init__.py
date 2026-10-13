"""The gateway between the assistant and the SLT MCP hub.

Every hub call goes through here: tool access by tier and service type,
the customer's number forced from the verified session, one hub connection
per signed in customer (the hub ties a login to a connection), an audit line
per call, and no retries for anything that changes an account.

    call(name, arguments, access)            a tool call, never raises
    begin_login / finish_login / end_login  the sign in wizard's path
    service_type, tool_schemas              what the model may see and use
    has_session, drop_session               the customer's connection
    status, latencies                       /health and the admin page
"""

from .gateway import (
    GatewayResult,
    LoginResult,
    LoginStart,
    begin_login,
    call,
    drop_session,
    end_login,
    finish_login,
    has_session,
    latencies,
    service_type,
    status,
    tool_schemas,
)

__all__ = [
    "call", "begin_login", "finish_login", "end_login", "service_type", "tool_schemas", "status",
    "latencies", "GatewayResult", "LoginStart", "LoginResult", "has_session", "drop_session",
]
