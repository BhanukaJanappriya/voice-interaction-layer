"""MCP server: OAuth client credentials, discovery metadata, bearer auth on
/mcp, per tool scopes, revocation, and the audit trail.

Runs against a small FastAPI app built with mcp_server.mount.mount_mcp and
fake tool callables (deterministic, no retriever or LLM), exactly the way
server.py wires it. A separate check confirms server.app serves /mcp once
integrated.
"""

import base64
import json
from contextlib import asynccontextmanager

import pytest

from conftest import BASE_URL, app_paths, make_client, optional_import

QUESTION_MARKER = "ZEBRA-QUESTION-TEXT-991"
ANSWER_MARKER = "OKAPI-ANSWER-TEXT-117"
PROTOCOL_VERSION = "2025-11-25"
ACCEPT = "application/json, text/event-stream"


@pytest.fixture(scope="module")
def mount():
    module = optional_import("mcp_server.mount")
    if module is None or not hasattr(module, "mount_mcp"):
        pytest.skip("mcp_server.mount.mount_mcp not delivered yet (workstream D)")
    return module


class ToolCalls:
    def __init__(self):
        self.asked = []

    def search(self, query, top_k):
        return [
            {
                "title": "Roaming",
                "text": "Roaming packs are available.",
                "score": 0.91,
                "source_url": "https://www.sltmobitel.lk/roaming",
                "retrieved_at": "2026-09-30",
                "metadata": {"internal": "must not leak"},
            }
        ][:top_k]

    def packages(self, name):
        return [{"name": "Roaming Saver", "price": "Rs 999", "source_url": "https://www.sltmobitel.lk/roaming"}]

    def ask(self, question, language):
        self.asked.append((question, language))
        return f"{ANSWER_MARKER} reply", "fake mode"


@pytest.fixture(scope="module")
def tool_calls():
    return ToolCalls()


@pytest.fixture(scope="module")
def mcp_app(mount, tool_calls, db_schema):
    from fastapi import FastAPI

    holder = {}

    @asynccontextmanager
    async def lifespan(_app):
        async with holder["handle"].lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    holder["handle"] = mount.mount_mcp(app, tool_calls.search, tool_calls.packages, tool_calls.ask)
    if not holder["handle"].enabled:
        pytest.skip("MCP disabled")
    return app


@pytest.fixture(scope="module")
def mcp_http(mcp_app):
    from fastapi.testclient import TestClient

    with TestClient(mcp_app, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def clients_module():
    return optional_import("mcp_server.clients")


@pytest.fixture
def new_mcp_client(clients_module):
    def _create(scopes=("knowledge:read", "assistant:ask"), name="QA client"):
        row, secret = clients_module.create_client(name, list(scopes))
        return row, secret

    return _create


def _token(http, client_id, secret, scope=None, basic=False):
    data = {"grant_type": "client_credentials"}
    headers = {}
    if basic:
        raw = f"{client_id}:{secret}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    else:
        data.update({"client_id": client_id, "client_secret": secret})
    if scope is not None:
        data["scope"] = scope
    return http.post("/oauth/token", data=data, headers=headers)


@pytest.fixture
def get_token(mcp_http, new_mcp_client):
    def _get(scopes=("knowledge:read", "assistant:ask")):
        row, secret = new_mcp_client(scopes)
        response = _token(mcp_http, row.client_id, secret)
        assert response.status_code == 200, response.text
        return response.json()["access_token"], row

    return _get


def _rpc(http, token, method, params=None, request_id=1):
    headers = {
        "Accept": ACCEPT,
        "Content-Type": "application/json",
        "mcp-protocol-version": PROTOCOL_VERSION,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return http.post("/mcp", content=json.dumps(message), headers=headers)


def _rpc_body(response):
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise AssertionError(f"no data in SSE response {response.text!r}")
    return response.json()


def _initialize(http, token):
    response = _rpc(
        http,
        token,
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "qa", "version": "1"}},
    )
    assert response.status_code == 200, response.text
    return _rpc_body(response)


def _audit_rows(db_schema):
    from db.models import AuditLog

    with db_schema.session_scope() as session:
        return [
            {
                "actor_type": row.actor_type,
                "actor_id": row.actor_id,
                "action": row.action,
                "outcome": row.outcome,
                "detail": row.detail,
                "mcp_client_id": row.mcp_client_id,
            }
            for row in session.query(AuditLog).order_by(AuditLog.id).all()
        ]


# OAuth token endpoint


def test_token_happy_path(mcp_http, new_mcp_client, db_schema):
    from auth.tokens import AUDIENCE_MCP, decode_token, token_scopes

    row, secret = new_mcp_client(("knowledge:read",))
    response = _token(mcp_http, row.client_id, secret)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"].lower() == "bearer"
    assert body["expires_in"] > 0
    assert body["scope"] == "knowledge:read"
    claims = decode_token(body["access_token"], AUDIENCE_MCP)
    assert token_scopes(claims) == {"knowledge:read"}
    assert response.headers.get("cache-control") == "no-store"
    rows = _audit_rows(db_schema)
    assert any(r["action"] == "oauth_token" and r["outcome"] == "ok" for r in rows)


def test_token_with_basic_auth_and_narrowed_scope(mcp_http, new_mcp_client):
    row, secret = new_mcp_client()
    response = _token(mcp_http, row.client_id, secret, scope="knowledge:read", basic=True)
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "knowledge:read"


def test_token_invalid_client(mcp_http, new_mcp_client, db_schema):
    row, _ = new_mcp_client()
    for client_id, secret in ((row.client_id, "wrong-secret"), ("mcp_unknown", "whatever")):
        response = _token(mcp_http, client_id, secret)
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_client"
    assert any(r["outcome"] == "denied" and r["action"] == "oauth_token" for r in _audit_rows(db_schema))


def test_token_invalid_scope(mcp_http, new_mcp_client):
    row, secret = new_mcp_client(("knowledge:read",))
    response = _token(mcp_http, row.client_id, secret, scope="knowledge:read assistant:ask")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


def test_token_unsupported_grant_type(mcp_http, new_mcp_client):
    row, secret = new_mcp_client()
    response = mcp_http.post(
        "/oauth/token",
        data={"grant_type": "password", "client_id": row.client_id, "client_secret": secret},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


def test_token_refused_for_revoked_client(mcp_http, new_mcp_client, clients_module):
    row, secret = new_mcp_client()
    clients_module.revoke_client(row.id)
    response = _token(mcp_http, row.client_id, secret)
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


# Discovery metadata


@pytest.mark.parametrize(
    "path", ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"]
)
def test_protected_resource_metadata(mcp_http, path):
    response = mcp_http.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == f"{BASE_URL}/mcp"
    assert BASE_URL in body["authorization_servers"]
    assert set(body["scopes_supported"]) == {"knowledge:read", "assistant:ask"}


def test_authorization_server_metadata(mcp_http):
    body = mcp_http.get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == BASE_URL
    assert body["token_endpoint"] == f"{BASE_URL}/oauth/token"
    assert "client_credentials" in body["grant_types_supported"]


# Bearer auth on /mcp


def test_mcp_without_token_is_401_with_www_authenticate(mcp_http):
    response = _rpc(mcp_http, None, "tools/list")
    assert response.status_code == 401
    challenge = response.headers.get("www-authenticate", "")
    assert "Bearer" in challenge
    assert "resource_metadata" in challenge
    assert "/.well-known/oauth-protected-resource" in challenge


def test_mcp_bad_tokens_are_401(mcp_http, new_mcp_client):
    from auth.tokens import AUDIENCE_ADMIN, AUDIENCE_MCP, issue_token

    row, _ = new_mcp_client()
    admin_token, _ = issue_token("1", AUDIENCE_ADMIN, 5)
    expired, _ = issue_token(row.client_id, AUDIENCE_MCP, -1, scopes=["knowledge:read"])
    unknown_client, _ = issue_token("mcp_nobody", AUDIENCE_MCP, 5, scopes=["knowledge:read"])
    for token in ("garbage", admin_token, expired, unknown_client):
        response = _rpc(mcp_http, token, "tools/list")
        assert response.status_code == 401, (token[:20], response.status_code)
        assert "Bearer" in response.headers.get("www-authenticate", "")


def test_initialize_list_and_call(mcp_http, get_token, tool_calls, db_schema):
    token, row = get_token()
    init = _initialize(mcp_http, token)
    assert "result" in init, init
    assert init["result"]["serverInfo"]["name"]
    notified = _rpc(mcp_http, token, "notifications/initialized", request_id=None)
    assert notified.status_code in (200, 202)

    listed = _rpc_body(_rpc(mcp_http, token, "tools/list", {}, request_id=2))
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"search_knowledge", "get_package_details", "ask_assistant"} <= names

    search = _rpc_body(
        _rpc(mcp_http, token, "tools/call", {"name": "search_knowledge", "arguments": {"query": "roaming"}}, 3)
    )
    result = search["result"]
    assert not result.get("isError"), result
    text = json.dumps(result)
    assert "https://www.sltmobitel.lk/roaming" in text
    assert "must not leak" not in text

    asked = _rpc_body(
        _rpc(
            mcp_http,
            token,
            "tools/call",
            {"name": "ask_assistant", "arguments": {"question": f"{QUESTION_MARKER} roaming?", "language": "en"}},
            4,
        )
    )
    assert not asked["result"].get("isError"), asked
    assert ANSWER_MARKER in json.dumps(asked["result"])
    assert tool_calls.asked[-1] == (f"{QUESTION_MARKER} roaming?", "en")

    rows = [r for r in _audit_rows(db_schema) if r["mcp_client_id"] == row.id]
    actions = {(r["action"], r["outcome"]) for r in rows}
    assert ("search_knowledge", "ok") in actions
    assert ("ask_assistant", "ok") in actions
    assert all(r["actor_type"] == "mcp_client" and r["actor_id"] == row.client_id for r in rows)


def test_audit_rows_hold_no_user_content(mcp_http, get_token, db_schema):
    token, _ = get_token()
    _rpc(
        mcp_http,
        token,
        "tools/call",
        {"name": "ask_assistant", "arguments": {"question": f"{QUESTION_MARKER} private question"}},
        5,
    )
    _rpc(mcp_http, token, "tools/call", {"name": "search_knowledge", "arguments": {"query": QUESTION_MARKER}}, 6)
    dump = json.dumps(_audit_rows(db_schema))
    assert QUESTION_MARKER not in dump
    assert ANSWER_MARKER not in dump


def test_insufficient_scope_denied_and_audited(mcp_http, get_token, tool_calls, db_schema):
    token, row = get_token(("knowledge:read",))
    before = len(tool_calls.asked)
    response = _rpc(
        mcp_http, token, "tools/call", {"name": "ask_assistant", "arguments": {"question": "hi"}}, 7
    )
    if response.status_code == 403:
        assert "insufficient_scope" in response.headers.get("www-authenticate", "")
        body = response.json()
    else:
        body = _rpc_body(response)
    assert "insufficient_scope" in json.dumps(body)
    assert len(tool_calls.asked) == before, "the tool must not run"
    rows = [r for r in _audit_rows(db_schema) if r["mcp_client_id"] == row.id]
    assert any(r["action"] == "ask_assistant" and r["outcome"] == "denied" for r in rows), rows


def test_scoped_client_can_still_use_its_tools(mcp_http, get_token):
    token, _ = get_token(("knowledge:read",))
    body = _rpc_body(
        _rpc(mcp_http, token, "tools/call", {"name": "get_package_details", "arguments": {"name": "Roaming"}}, 8)
    )
    assert not body["result"].get("isError"), body
    assert "Roaming Saver" in json.dumps(body["result"])


def test_revoked_client_token_rejected_immediately(mcp_http, get_token, clients_module):
    token, row = get_token()
    assert _rpc(mcp_http, token, "tools/list", {}).status_code == 200
    clients_module.revoke_client(row.id)
    response = _rpc(mcp_http, token, "tools/list", {})
    assert response.status_code == 401


def test_invalid_params_reported_as_tool_error(mcp_http, get_token):
    token, _ = get_token()
    body = _rpc_body(
        _rpc(mcp_http, token, "tools/call", {"name": "search_knowledge", "arguments": {"query": "   "}}, 9)
    )
    assert body.get("error") or body["result"].get("isError"), body


def test_official_sdk_client(mount, tool_calls, get_token):
    """The official SDK client over streamable HTTP, through the same
    auth chain (an httpx client bound to the ASGI app, no network). Uses
    its own app, since the SDK runs each session manager only once."""
    import anyio
    httpx = optional_import("httpx2") or __import__("httpx")
    from fastapi import FastAPI

    streamable = optional_import("mcp.client.streamable_http")
    session_module = optional_import("mcp.client.session")
    factory = getattr(streamable, "streamable_http_client", None) or getattr(streamable, "streamablehttp_client", None)
    if factory is None or session_module is None:
        pytest.skip("SDK streamable HTTP client API not found")
    app = FastAPI()
    handle = mount.mount_mcp(app, tool_calls.search, tool_calls.packages, tool_calls.ask)
    token, _ = get_token()

    async def _run():
        async with handle.lifespan():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url=BASE_URL, headers={"Authorization": f"Bearer {token}"}
            ) as http_client:
                try:
                    context = factory(f"{BASE_URL}/mcp", http_client=http_client)
                except TypeError:
                    return None
                async with context as streams:
                    read, write = streams[0], streams[1]
                    async with session_module.ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        return {tool.name for tool in tools.tools}

    names = anyio.run(_run)
    if names is None:
        pytest.skip("SDK client factory does not accept an http_client")
    assert "search_knowledge" in names


# Integration into server.app


def test_server_app_serves_mcp(server_module):
    paths = app_paths(server_module.app)
    if "/mcp" not in paths:
        pytest.skip("/mcp not mounted in server.app yet (plan R3 integration pending)")
    assert {"/oauth/token", "/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server"} <= paths
    c = make_client(server_module.app)
    response = _rpc(c, None, "tools/list")
    c.close()
    assert response.status_code == 401
    assert "resource_metadata" in response.headers.get("www-authenticate", "")
