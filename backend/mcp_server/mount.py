"""Puts the MCP server and its OAuth endpoints into the FastAPI app.

Why not app.mount("/mcp", mcp.streamable_http_app()): the SDK's app is a
whole Starlette application with its own route at streamable_http_path,
so mounting it at /mcp serves /mcp/mcp, and pointing its path at "/"
makes a bare POST /mcp answer with a 307 redirect to /mcp/ (Starlette
Mount only matches below the prefix), which MCP clients refuse to follow
for a POST. Instead this module asks the SDK for its session manager and
builds the same auth chain the SDK builds (from the SDK's own classes),
plus ScopeGate, as one ASGI endpoint registered on the exact path /mcp:

    AuthenticationMiddleware(BearerAuthBackend(JwtTokenVerifier))
      -> AuthContextMiddleware       (token visible to the tools)
      -> RequireAuthMiddleware       (401 + WWW-Authenticate)
      -> ScopeGate                   (403 insufficient_scope per tool)
      -> StreamableHTTPASGIApp       (the SDK transport)

Transport mode: stateless HTTP with plain JSON responses. Every request
carries a bearer token that is verified on its own, the tools are simple
request/response calls (no server initiated sampling, elicitation or
progress streams), and stateless means no in memory session that is lost
on restart, pinned to one worker, or has to be bound to the principal
that opened it. JSON rather than SSE keeps each reply one ordinary HTTP
response, easy to read with curl and through proxies.

The SDK's session manager must be running while requests arrive; the app
lifespan enters McpHandle.lifespan() for that.
"""

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from fastapi import FastAPI
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.mcpserver import MCPServer
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route

import config
from mcp_server import clients
from mcp_server.oauth import MCP_PATH, resource_metadata_url, resource_url
from mcp_server.oauth import router as oauth_router
from mcp_server.server import AskFn, JwtTokenVerifier, PackageFn, ScopeGate, SearchFn, build_mcp_server


def _transport_security() -> TransportSecuritySettings:
    """DNS rebinding protection (the SDK checks Host and Origin): the
    loopback names on any port, plus the host in PUBLIC_BASE_URL, so the
    same code works on a laptop and behind a real hostname. A request
    addressed to any other Host gets 421 from the SDK."""
    hosts = {"127.0.0.1", "localhost", "[::1]"}
    origins = {"http://127.0.0.1", "http://localhost", "http://[::1]", "https://127.0.0.1", "https://localhost"}
    public = urlparse(config.PUBLIC_BASE_URL)
    allowed_hosts: set[str] = set()
    allowed_origins: set[str] = set()
    for host in hosts:
        allowed_hosts.update({host, f"{host}:*"})
    for origin in origins:
        allowed_origins.update({origin, f"{origin}:*"})
    if public.netloc:
        allowed_hosts.add(public.netloc)
        allowed_hosts.add(public.hostname if ":" not in (public.hostname or "") else f"[{public.hostname}]")
        allowed_origins.add(f"{public.scheme}://{public.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )


class McpHandle:
    """What mount_mcp returns: the lifespan to enter and the status for
    /health. When MCP is disabled it is inert (nothing mounted, lifespan
    does nothing), so server.py needs no if statements around it."""

    def __init__(self, enabled: bool, server: Optional[MCPServer] = None,
                 session_manager: Optional[StreamableHTTPSessionManager] = None):
        self.enabled = enabled
        self.server = server
        self.session_manager = session_manager
        self.running = False

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Runs the SDK's session manager for as long as the app is up.
        The SDK allows run() only once per manager, which matches one
        FastAPI lifespan per process."""
        if not self.enabled or self.session_manager is None:
            yield
            return
        started = time.perf_counter()
        async with self.session_manager.run():
            self.running = True
            print(f"MCP server ready at {resource_url()} ({clients.elapsed_ms(started)} ms)")
            try:
                yield
            finally:
                self.running = False

    def status(self) -> dict:
        info = {"enabled": self.enabled, "path": MCP_PATH if self.enabled else None, "running": self.running}
        if not self.enabled:
            return info
        try:
            info["active_clients"] = clients.count_active_clients()
        except Exception as exc:
            info["active_clients"] = None
            info["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return info


def _put_first(app: FastAPI, count_before: int) -> None:
    """Moves the routes added since count_before to the front of the
    route list, so a catch all mounted earlier (the static frontend at
    "/") cannot shadow /mcp or /oauth/token whatever order server.py
    wires things in."""
    routes = app.router.routes
    added = routes[count_before:]
    del routes[count_before:]
    routes[0:0] = added


def mount_mcp(app: FastAPI, search_fn: SearchFn, package_fn: PackageFn, ask_fn: AskFn) -> McpHandle:
    if not config.MCP_ENABLED:
        print("MCP server disabled (MCP_ENABLED=false)")
        return McpHandle(enabled=False)

    mcp = build_mcp_server(search_fn, package_fn, ask_fn)
    # Called for its side effect: it creates the session manager with
    # these transport options. The Starlette app it returns is not used
    # (see the module docstring).
    mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=_transport_security(),
    )
    session_manager = mcp.session_manager
    auth = mcp.settings.auth

    endpoint = AuthenticationMiddleware(
        AuthContextMiddleware(
            RequireAuthMiddleware(
                ScopeGate(StreamableHTTPASGIApp(session_manager), max_body_bytes=DEFAULT_MAX_REQUEST_BODY_SIZE),
                required_scopes=list(auth.required_scopes or []),
                resource_metadata_url=AnyHttpUrl(resource_metadata_url()),
            )
        ),
        backend=BearerAuthBackend(
            # Stateless, so a second instance is the same verifier the
            # MCPServer was configured with (the SDK keeps that one in a
            # private attribute).
            JwtTokenVerifier(),
            resource_server_url=auth.resource_server_url if auth.validate_token_resource else None,
        ),
    )

    count_before = len(app.router.routes)
    app.include_router(oauth_router)
    # An ASGI app (not a function) as the endpoint: Starlette passes the
    # request through untouched, and leaving methods unset lets the SDK
    # answer GET/DELETE/other methods itself.
    app.router.routes.append(Route(MCP_PATH, endpoint=endpoint, include_in_schema=False))
    _put_first(app, count_before)
    return McpHandle(enabled=True, server=mcp, session_manager=session_manager)

