"""The MCP server: three tools over this assistant's knowledge base and
Orchestrator, for other agents to call (requirement R3).

Built on the official MCP Python SDK 2.x (MCPServer, the renamed FastMCP).
What each tool actually does is injected as a plain callable
(search_fn, package_fn, ask_fn), so this package never imports server.py
or the knowledge package, and a test can run the whole MCP stack against
fakes without Ollama, Whisper or a built index.

Security model, in the order a request meets it (wired up in mount.py):
  1. JwtTokenVerifier (the SDK's TokenVerifier protocol) checks the bearer
     token's signature, expiry, issuer and audience through
     auth.tokens.decode_token, and that the client still exists and is not
     revoked, so revoking a client in the admin portal takes effect on its
     very next request rather than when its token expires.
  2. The SDK's RequireAuthMiddleware turns a missing or rejected token into
     HTTP 401 with WWW-Authenticate: Bearer ... resource_metadata="...".
  3. ScopeGate (below) looks inside a tools/call request before the SDK
     does and answers HTTP 403 with WWW-Authenticate error="insufficient_scope"
     when the token lacks the tool's scope, as the MCP authorization spec
     asks. The SDK's own middleware can only require one scope set for the
     whole endpoint, not one per tool.
  4. Each tool checks its scope again from the SDK's auth context before
     running (defense in depth: if the gate were ever bypassed, for example
     by JSON the gate and the SDK parse differently, the tool itself still
     refuses, as an MCP tool error naming insufficient_scope).
Every tool call writes one audit row (client, tool, outcome, duration), and
never the query, question or answer text.
"""

import json
import time
from typing import Any, Callable, Optional

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing_extensions import Annotated, TypedDict

import config
from auth.tokens import AUDIENCE_MCP, TokenError, decode_token, token_scopes
from mcp_server import clients
from mcp_server.oauth import resource_metadata_url, resource_url

SCOPE_KNOWLEDGE_READ = "knowledge:read"
SCOPE_ASSISTANT_ASK = "assistant:ask"

# The one place that says which scope each tool needs; used by both the
# HTTP level gate and the in tool check.
TOOL_SCOPES = {
    "search_knowledge": SCOPE_KNOWLEDGE_READ,
    "get_package_details": SCOPE_KNOWLEDGE_READ,
    "ask_assistant": SCOPE_ASSISTANT_ASK,
}

MAX_QUERY_CHARS = 500
MAX_PACKAGE_NAME_CHARS = 100
MAX_QUESTION_CHARS = 2000
MIN_TOP_K, MAX_TOP_K = 1, 10

# JSON-RPC has no standard code for "not allowed"; this one sits in the
# implementation defined server error range (-32000 to -32099). The
# machine readable reason is in error.data.error.
INSUFFICIENT_SCOPE_CODE = -32003

SearchFn = Callable[[str, int], list[dict]]
PackageFn = Callable[[str], list[dict]]
AskFn = Callable[[str, Optional[str]], tuple[str, str]]

_RESULT_KEYS = ("title", "text", "score", "source_url", "retrieved_at")


# Return types as TypedDicts so the SDK publishes an output schema and
# sends structuredContent alongside the text content.
class KnowledgeChunk(TypedDict):
    title: Optional[str]
    text: Optional[str]
    score: Optional[float]
    source_url: Optional[str]
    retrieved_at: Optional[str]


class SearchResult(TypedDict):
    results: list[KnowledgeChunk]


class PackageResult(TypedDict):
    packages: list[dict[str, Any]]


class AskResult(TypedDict):
    reply: str
    mode: str


class JwtTokenVerifier:
    """The SDK's TokenVerifier protocol, backed by this app's own JWTs.
    Returns None (never raises) for any bad token, which the SDK turns
    into a 401 with the resource metadata pointer."""

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        try:
            claims = decode_token(token, AUDIENCE_MCP)
        except TokenError:
            return None
        client_id = str(claims.get("sub") or "")
        try:
            # A database lookup, so off the event loop.
            client_pk = await anyio.to_thread.run_sync(clients.active_client_pk, client_id)
        except Exception as exc:
            print(f"MCP token check could not reach the database ({type(exc).__name__}); refusing the token")
            return None
        if client_pk is None:
            return None
        return AccessToken(
            token=token,
            client_id=client_id,
            subject=client_id,
            scopes=sorted(token_scopes(claims)),
            expires_at=int(claims["exp"]),
            resource=AUDIENCE_MCP,
            claims={"iss": claims.get("iss"), "client_pk": client_pk, "jti": claims.get("jti")},
        )


def _caller() -> tuple[str, Optional[int], set[str]]:
    """(client_id, client database id, scopes) for the request being
    served, from the SDK's auth context. An unauthenticated context (only
    possible if the server is used without mount.py's auth chain) has no
    scopes, so every tool refuses it."""
    token = get_access_token()
    if token is None:
        return "anonymous", None, set()
    return token.client_id, (token.claims or {}).get("client_pk"), set(token.scopes)


def _insufficient_scope_message(tool: str, required: str) -> str:
    return f"insufficient_scope: tool '{tool}' requires scope '{required}'"


async def _audit(client_id: str, client_pk: Optional[int], tool: str, outcome: str, detail: Optional[str], started: float) -> None:
    await anyio.to_thread.run_sync(
        lambda: clients.record_audit(
            actor_id=client_id,
            action=tool,
            outcome=outcome,
            detail=detail,
            duration_ms=clients.elapsed_ms(started),
            mcp_client_pk=client_pk,
        )
    )


async def _run_tool(tool: str, validate: Callable[[], Optional[str]], work: Callable[[], Any], summarize: Callable[[Any], str]) -> Any:
    """The shared frame around every tool: scope check, input validation,
    running the blocking callable on a worker thread, one audit row, one
    latency line. validate returns an error message or None; summarize
    turns the result into a content free audit detail (counts only)."""
    started = time.perf_counter()
    client_id, client_pk, scopes = _caller()
    required = TOOL_SCOPES[tool]
    if required not in scopes:
        await _audit(client_id, client_pk, tool, "denied", f"insufficient_scope: needs {required}", started)
        raise ToolError(_insufficient_scope_message(tool, required))
    problem = validate()
    if problem:
        await _audit(client_id, client_pk, tool, "error", f"invalid_params: {problem}", started)
        raise ToolError(f"invalid_params: {problem}")
    try:
        result = await anyio.to_thread.run_sync(work)
    except Exception as exc:
        await _audit(client_id, client_pk, tool, "error", type(exc).__name__, started)
        print(f"MCP tool {tool} failed for {client_id} after {clients.elapsed_ms(started)} ms: {type(exc).__name__}")
        raise ToolError(f"{tool} is temporarily unavailable; try again later") from exc
    await _audit(client_id, client_pk, tool, "ok", summarize(result), started)
    print(f"MCP tool {tool} for {client_id}: {clients.elapsed_ms(started)} ms")
    return result


def _clean_result(item: dict) -> dict:
    """Only the documented fields, so retriever internals (metadata,
    vectors) never leak to a client; dates as strings, score as float."""
    cleaned = {key: item.get(key) for key in _RESULT_KEYS}
    if cleaned["score"] is not None:
        cleaned["score"] = float(cleaned["score"])
    if cleaned["retrieved_at"] is not None:
        cleaned["retrieved_at"] = str(cleaned["retrieved_at"])
    return cleaned


def build_mcp_server(search_fn: SearchFn, package_fn: PackageFn, ask_fn: AskFn) -> MCPServer:
    """An MCPServer with the three tools, configured as an OAuth resource
    server for PUBLIC_BASE_URL/mcp with JwtTokenVerifier. It is not served
    by itself: mount.py puts its streamable HTTP transport behind the auth
    chain at /mcp in the FastAPI app."""
    mcp = MCPServer(
        name="slt-mobitel-voice-assistant",
        title="SLT Mobitel voice assistant",
        instructions=(
            "Tools over the SLT Mobitel knowledge base and the voice assistant. "
            "search_knowledge and get_package_details return source URLs and retrieval dates; "
            "prices change, so cite the source. ask_assistant runs the full assistant."
        ),
        token_verifier=JwtTokenVerifier(),
        auth=AuthSettings(
            issuer_url=config.PUBLIC_BASE_URL,
            resource_server_url=resource_url(),
            # Endpoint wide scopes would lock out a client that only has
            # one of the two; scopes are enforced per tool instead.
            required_scopes=[],
            # The verifier sets AccessToken.resource from the checked
            # audience, so the SDK can confirm it too.
            validate_token_resource=True,
        ),
    )

    @mcp.tool(
        description=(
            "Semantic search over the SLT Mobitel knowledge base (packages, prices, FAQs, company info). "
            f"query: 1 to {MAX_QUERY_CHARS} characters, any of English, Sinhala or Tamil. "
            f"top_k: {MIN_TOP_K} to {MAX_TOP_K}. Returns chunks with title, text, score, source_url and "
            f"retrieved_at. Requires scope {SCOPE_KNOWLEDGE_READ}."
        )
    )
    async def search_knowledge(
        query: Annotated[str, Field(description="What to search for")],
        top_k: Annotated[int, Field(description=f"How many results, {MIN_TOP_K} to {MAX_TOP_K}")] = 4,
    ) -> SearchResult:
        query_text = (query or "").strip()

        def validate() -> Optional[str]:
            if not query_text:
                return "query must not be empty"
            if len(query_text) > MAX_QUERY_CHARS:
                return f"query is longer than {MAX_QUERY_CHARS} characters"
            if not isinstance(top_k, int) or not MIN_TOP_K <= top_k <= MAX_TOP_K:
                return f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}"
            return None

        results = await _run_tool(
            "search_knowledge",
            validate,
            lambda: [_clean_result(item) for item in (search_fn(query_text, top_k) or [])],
            lambda found: f"top_k={top_k} results={len(found)}",
        )
        return {"results": results}

    @mcp.tool(
        description=(
            "Structured details (name, price, validity, data allowance, conditions, source_url, retrieved_at) "
            f"for SLT Mobitel packages whose name matches. name: 1 to {MAX_PACKAGE_NAME_CHARS} characters. "
            f"Requires scope {SCOPE_KNOWLEDGE_READ}."
        )
    )
    async def get_package_details(name: Annotated[str, Field(description="Package name or part of it")]) -> PackageResult:
        name_text = (name or "").strip()

        def validate() -> Optional[str]:
            if not name_text:
                return "name must not be empty"
            if len(name_text) > MAX_PACKAGE_NAME_CHARS:
                return f"name is longer than {MAX_PACKAGE_NAME_CHARS} characters"
            return None

        packages = await _run_tool(
            "get_package_details",
            validate,
            lambda: list(package_fn(name_text) or []),
            lambda found: f"matches={len(found)}",
        )
        return {"packages": packages}

    @mcp.tool(
        description=(
            "Ask the SLT Mobitel voice assistant a question and get its text reply, grounded in the "
            f"knowledge base. question: 1 to {MAX_QUESTION_CHARS} characters. language: optional, one of "
            f"{', '.join(sorted(config.SUPPORTED_LANGUAGES))} (reply language). Returns reply and mode "
            f"(which model path answered). Requires scope {SCOPE_ASSISTANT_ASK}."
        )
    )
    async def ask_assistant(
        question: Annotated[str, Field(description="The question to ask")],
        language: Annotated[Optional[str], Field(description="Reply language: en, si or ta")] = None,
    ) -> AskResult:
        question_text = (question or "").strip()
        language_code = (language or "").strip().lower() or None

        def validate() -> Optional[str]:
            if not question_text:
                return "question must not be empty"
            if len(question_text) > MAX_QUESTION_CHARS:
                return f"question is longer than {MAX_QUESTION_CHARS} characters"
            if language_code is not None and language_code not in config.SUPPORTED_LANGUAGES:
                return f"language must be one of {', '.join(sorted(config.SUPPORTED_LANGUAGES))}"
            return None

        reply, mode = await _run_tool(
            "ask_assistant",
            validate,
            lambda: ask_fn(question_text, language_code),
            lambda answer: f"language={language_code or 'auto'} mode={answer[1]}",
        )
        return {"reply": reply, "mode": mode}

    return mcp


class ScopeGate:
    """ASGI middleware in front of the SDK's streamable HTTP handler that
    refuses a tools/call for a tool whose scope the bearer token lacks
    with HTTP 403 and WWW-Authenticate: Bearer error="insufficient_scope",
    scope="<required>", resource_metadata="...", the response the MCP
    authorization spec describes for a token with too little scope.

    The body is a JSON-RPC error too, so the official SDK client (which
    reads a JSON-RPC error out of a non 2xx JSON response) raises an
    MCPError saying insufficient_scope instead of a bare HTTP failure.

    Must run after authentication (scope["user"] is the SDK's
    AuthenticatedUser). The body it reads is replayed unchanged to the
    wrapped app, and anything it cannot parse is passed through for the
    SDK to reject in its usual way.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        body = b""
        more_body = True
        disconnected: Optional[Message] = None
        while more_body and len(body) <= self.max_body_bytes:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = message
                break
            body += message.get("body", b"")
            more_body = message.get("more_body", False)

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected is not None:
                    return disconnected
                return {"type": "http.request", "body": body, "more_body": more_body}
            return await receive()

        if disconnected is None and not more_body:
            denial = self._find_denial(body, scope.get("user"))
            if denial is not None:
                await self._deny(scope, send, *denial)
                return
        await self.app(scope, replay, send)

    @staticmethod
    def _find_denial(body: bytes, user: Any) -> Optional[tuple[Any, str, str]]:
        """(request id, tool, required scope) for the first tools/call in
        the body that the token may not make, or None."""
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        messages = payload if isinstance(payload, list) else [payload]
        granted = set(user.scopes) if isinstance(user, AuthenticatedUser) else set()
        for message in messages:
            if not isinstance(message, dict) or message.get("method") != "tools/call":
                continue
            params = message.get("params")
            tool = params.get("name") if isinstance(params, dict) else None
            required = TOOL_SCOPES.get(tool) if isinstance(tool, str) else None
            if required and required not in granted:
                return message.get("id"), tool, required
        return None

    @staticmethod
    async def _deny(scope: Scope, send: Send, request_id: Any, tool: str, required: str) -> None:
        started = time.perf_counter()
        user = scope.get("user")
        token = user.access_token if isinstance(user, AuthenticatedUser) else None
        client_id = token.client_id if token else "anonymous"
        client_pk = (token.claims or {}).get("client_pk") if token else None
        message = _insufficient_scope_message(tool, required)
        await _audit(client_id, client_pk, tool, "denied", f"insufficient_scope: needs {required} (http 403)", started)
        print(f"MCP tool {tool} refused for {client_id}: needs scope {required}")

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": INSUFFICIENT_SCOPE_CODE,
                    "message": message,
                    "data": {"error": "insufficient_scope", "required_scope": required, "tool": tool},
                },
            }
        ).encode("utf-8")
        www_authenticate = (
            f'Bearer error="insufficient_scope", scope="{required}", '
            f'resource_metadata="{resource_metadata_url()}", '
            f'error_description="Tool {tool} requires scope {required}"'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", www_authenticate.encode("utf-8")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
