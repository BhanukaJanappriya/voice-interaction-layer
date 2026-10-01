"""Self test for the external agent work (url_safety.py, the
ExternalHttpAgent section of agent.py, admin/service.py, admin/routes.py).

Runs against a throwaway SQLite database in a temporary directory and a
small stub HTTP agent on 127.0.0.1, so it touches nothing real:

    venv/Scripts/python.exe backend/admin/_selftest.py

The repository's pytest suite lives in tests/ (owned separately); this
file only proves this workstream's pieces before they are handed over.
"""

import gzip
import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# Configure before config.py is imported anywhere.
_TMP = tempfile.mkdtemp(prefix="admin-selftest-")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP, 'test.db').as_posix()}"
os.environ["EXTERNAL_AGENT_ALLOW_PRIVATE"] = "true"
os.environ["EXTERNAL_AGENT_MAX_RESPONSE_BYTES"] = "200000"
os.environ["WARM_UP_ON_STARTUP"] = "false"
os.environ.pop("FIELD_ENCRYPTION_KEY", None)

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import config  # noqa: E402
import url_safety  # noqa: E402
from agent import (  # noqa: E402
    ExternalAgentError,
    ExternalHttpAgent,
    extract_response_path,
    iter_sse_events,
    render_body_template,
    sse_text_pieces,
)

RESULTS = {"passed": 0, "failed": 0}


def check(name, condition, info=""):
    if condition:
        RESULTS["passed"] += 1
        print(f"  ok    {name}")
    else:
        RESULTS["failed"] += 1
        print(f"  FAIL  {name} {info}")


def raises(fn, exc_type, contains=""):
    try:
        fn()
    except exc_type as exc:
        return contains.lower() in str(exc).lower() or (print(f"        message was: {exc}") and False)
    except Exception as exc:  # wrong type
        print(f"        raised {type(exc).__name__}: {exc}")
        return False
    return False


# 1. url_safety

def test_url_safety():
    print("url_safety")
    refuse = {
        "http://127.0.0.1/": "loopback",
        "http://10.1.2.3/": "private",
        "http://192.168.1.1/": "private",
        "http://172.16.0.1/": "private",
        "http://169.254.169.254/latest/meta-data/": "metadata",
        "http://169.254.10.10/": "link local",
        "http://[::1]/": "loopback",
        "http://[::ffff:127.0.0.1]/": "loopback",
        "http://[::ffff:169.254.169.254]/": "metadata",
        "http://[fe80::1]/": "link local",
        "http://[fd00::1]/": "private",
        "http://100.64.1.1/": "carrier grade",
        "http://0.0.0.0/": "unspecified",
        "http://224.0.0.1/": "multicast",
        "http://240.0.0.1/": "",
        # Decimal IPv4: resolves to 127.0.0.1 on Linux, unresolvable on
        # Windows; refused either way.
        "http://2130706433/": "",
        "http://localhost/": "loopback",
        "ftp://example.com/": "http and https",
        "file:///etc/passwd": "http and https",
        "gopher://8.8.8.8/": "http and https",
        "http://user:pw@8.8.8.8/": "credentials",
        "http://@8.8.8.8/": "credentials",
        "http://8.8.8.8:99999/": "malformed",
        "http:///nohost": "no host",
        "": "empty",
    }
    for url, reason in refuse.items():
        check(f"refuses {url!r}", raises(lambda: url_safety.check_outbound_url(url, allow_private=False), url_safety.UnsafeUrlError, reason))
    check("allows public 8.8.8.8", url_safety.check_outbound_url("https://8.8.8.8/x", allow_private=False) is None)
    check("allow_private lets 127.0.0.1 through", url_safety.check_outbound_url("http://127.0.0.1:1/", allow_private=True) is None)
    check(
        "metadata refused even with allow_private",
        raises(lambda: url_safety.check_outbound_url("http://169.254.169.254/", allow_private=True), url_safety.UnsafeUrlError, "metadata"),
    )

    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host == "internal.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 80)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))]
        return real(host, *args, **kwargs)

    url_safety.socket.getaddrinfo = fake
    try:
        check(
            "refuses DNS name with one private record among public ones",
            raises(lambda: url_safety.check_outbound_url("http://internal.example/", allow_private=False), url_safety.UnsafeUrlError, "private"),
        )
    finally:
        url_safety.socket.getaddrinfo = real
    check(
        "unresolvable host refused",
        raises(lambda: url_safety.check_outbound_url("http://no-such-host.invalid/", allow_private=False), url_safety.UnsafeUrlError, "resolved"),
    )


# 2. Pure helpers

def test_helpers():
    print("template, response path, SSE parsing")
    nasty = 'He said "hi"\n\\ then {language} ", "admin": true, "x": "'
    body = render_body_template('{"message": "{text}", "user": "{user_name}", "lang": "{language}"}', nasty, 'Jo "J"', "si")
    check("template escapes quotes/newlines, no injected keys", body == {"message": nasty, "user": 'Jo "J"', "lang": "si"}, body)
    check("missing values become empty strings", render_body_template('{"m": "{text}", "u": "{user_name}"}', "x") == {"m": "x", "u": ""})
    check("Sinhala text passes through", render_body_template('{"m": "{text}"}', "ආයුබෝවන්") == {"m": "ආයුබෝවන්"})
    check("placeholder outside quotes rejected", raises(lambda: render_body_template('{"m": {text}}', "hi"), ExternalAgentError, "valid JSON"))
    check("nested template ok", render_body_template('{"messages": [{"role": "user", "content": "{text}"}]}', "a") == {"messages": [{"role": "user", "content": "a"}]})

    data = {"choices": [{"message": {"content": "hello"}}, {"message": {"content": "second"}}], "n": 3}
    check("path choices.0.message.content", extract_response_path(data, "choices.0.message.content") == "hello")
    check("negative index", extract_response_path(data, "choices.-1.message.content") == "second")
    check("empty path returns whole", extract_response_path(data, "") is data)
    check("missing key error", raises(lambda: extract_response_path(data, "reply"), ExternalAgentError, "no 'reply'"))
    check("index out of range", raises(lambda: extract_response_path(data, "choices.5"), ExternalAgentError, "out of range"))
    check("non numeric list index", raises(lambda: extract_response_path(data, "choices.first"), ExternalAgentError, "number"))

    sinhala = "ආයුබෝවන්".encode("utf-8")
    raw = (
        b": comment\r\n"
        b"data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\r\n\r\n"
        b"data: {\"choices\":[{\"delta\":{\"content\":\"Hel\"}}]}\n\n"
        b"data: {\"choices\":[{\"delta\":{\"content\":\"lo \"}}]}\n\n"
        b"data: " + sinhala + b"\n\n"
        b"event: ping\ndata: \"quoted\"\n\n"
        b"data: line one\ndata: line two\n\n"
        b"data: [DONE]\n\n"
        b"data: after done\n\n"
    )
    chunks = [raw[i:i + 7] for i in range(0, len(raw), 7)]  # splits inside UTF-8 sequences too
    pieces = list(sse_text_pieces(iter_sse_events(chunks), "choices.0.delta.content"))
    check("SSE pieces parsed in order, [DONE] stops", pieces == ["Hel", "lo ", "ආයුබෝවන්", "quoted", "line one\nline two"], pieces)
    check("SSE trailing event without blank line", list(iter_sse_events([b"data: tail"])) == [("message", "tail")])
    check("SSE error event raises", raises(lambda: list(sse_text_pieces([("error", "{}")], "x")), ExternalAgentError, "error"))


# 3. Stub agent server

class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def _json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/get":
            query = parse_qs(parts.query)
            self._json(200, {"reply": f"GET {query.get('q', [''])[0]} {query.get('lang', [''])[0]}"})
        elif parts.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.end_headers()
        else:
            self._json(404, {"error": "no"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/json":
            if self.headers.get("X-Api-Key") not in (None, "s3cret"):
                self._json(401, {"error": "bad key"})
                return
            body = self._body()
            self._json(200, {"reply": f"You said: {body.get('message')}", "key": self.headers.get("X-Api-Key")})
        elif path == "/secured":
            if self.headers.get("X-Api-Key") != "s3cret":
                self._json(401, {"error": "bad key"})
                return
            self._json(200, {"reply": "authorized"})
        elif path == "/openai":
            self._body()
            self._json(200, {"choices": [{"message": {"content": "from openai shape"}}]})
        elif path == "/sse":
            self._body()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for word in ["First. ", "Second. ", "Third."]:
                self.wfile.write(f'data: {{"delta": "{word}"}}\n\n'.encode())
                self.wfile.flush()
                time.sleep(0.5)
            self.wfile.write(b"data: [DONE]\n\n")
        elif path == "/sse-empty":
            self._body()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: [DONE]\n\n")
        elif path == "/big":
            self._body()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # No Content-Length: the body runs until the connection closes,
            # so only the reader's own cap can stop it.
            self.end_headers()
            try:
                self.wfile.write(b'{"reply": "' + b"a" * 400_000 + b'"}')
            except OSError:
                pass
        elif path == "/gzip-big":
            self._body()
            packed = gzip.compress(b'{"reply": "' + b"a" * 1_000_000 + b'"}')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(packed)))
            self.end_headers()
            try:
                self.wfile.write(packed)
            except OSError:
                pass
        elif path == "/slow":
            self._body()
            time.sleep(3)
            try:
                self._json(200, {"reply": "late"})
            except OSError:
                pass
        elif path == "/500":
            self._json(500, {"error": "boom"})
        elif path == "/notjson":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>hi</html>")
        elif path == "/plain":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("plain words".encode())
        else:
            self._json(404, {"error": "no"})


def start_stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_live_agent(base):
    print("ExternalHttpAgent against a stub server")
    agent = ExternalHttpAgent("stub", f"{base}/json", auth_header_name="X-Api-Key", auth_header_value="s3cret")
    reply = agent.respond('quote " and\nnewline')
    check("POST JSON reply and auth header sent", reply == 'You said: quote " and\nnewline', reply)
    check("repr hides auth value", "s3cret" not in repr(agent))

    agent = ExternalHttpAgent("openai", f"{base}/openai", body_template='{"messages":[{"role":"user","content":"{text}"}]}', response_path="choices.0.message.content")
    check("dotted path with list index", agent.respond("x") == "from openai shape")

    agent = ExternalHttpAgent("get", f"{base}/get", method="GET", body_template='{"q": "{text}", "lang": "{language}"}')
    check("GET sends template fields as query", agent.respond("hello there", language="ta") == "GET hello there ta")

    agent = ExternalHttpAgent("sse", f"{base}/sse", response_path="delta", streaming=True)
    start = time.perf_counter()
    stream = agent.respond_stream("hi")
    first = next(stream)
    first_at = time.perf_counter() - start
    rest = list(stream)
    check("SSE first piece before the stream ends", first == "First. " and first_at < 0.45, f"{first!r} at {first_at:.2f}s")
    check("SSE all pieces", [first] + rest == ["First. ", "Second. ", "Third."], rest)

    agent = ExternalHttpAgent("sse-fallback", f"{base}/json", streaming=True)
    check("streaming agent answering plain JSON falls back", agent.respond("x") == "You said: x")
    agent = ExternalHttpAgent("sse-empty", f"{base}/sse-empty", streaming=True)
    check("empty SSE stream is an error", raises(lambda: agent.respond("x"), ExternalAgentError, "without any reply"))

    cases = [
        ("size cap with no Content-Length", f"{base}/big", {}, "byte limit"),
        ("size cap counts decompressed bytes", f"{base}/gzip-big", {}, "byte limit"),
        ("read timeout", f"{base}/slow", {"timeout_seconds": 1}, "within 1 seconds"),
        ("redirect not followed", f"{base}/redirect", {"method": "GET"}, "redirect"),
        ("HTTP 500", f"{base}/500", {}, "HTTP 500"),
        ("not JSON", f"{base}/notjson", {}, "not valid JSON"),
        ("missing response path", f"{base}/json", {"response_path": "answer.text"}, "no 'answer'"),
        ("wrong auth", f"{base}/json", {"auth_header_name": "X-Api-Key", "auth_header_value": "wrong"}, "HTTP 401"),
        ("connection refused", "http://127.0.0.1:9/", {}, "could not connect"),
        ("SSRF metadata refused", "http://169.254.169.254/latest", {}, "metadata"),
        ("non http scheme", "ftp://example.com/", {}, "http and https"),
    ]
    for name, url, kwargs, expected in cases:
        a = ExternalHttpAgent(name, url, **kwargs)
        start = time.perf_counter()
        ok = raises(lambda: a.respond("x"), ExternalAgentError, expected)
        check(f"{name} -> ExternalAgentError ({time.perf_counter() - start:.2f}s)", ok)
    check("plain text reply with empty response path", ExternalHttpAgent("p", f"{base}/plain", response_path="").respond("x") == "plain words")

    # DNS rebinding: the pre check sees a public address, the connection
    # attempt then resolves to loopback and must be refused at connect time.
    config.EXTERNAL_AGENT_ALLOW_PRIVATE = False
    real = socket.getaddrinfo
    calls = {"n": 0}

    def rebinding(host, port, *args, **kwargs):
        if host == "rebind.example":
            calls["n"] += 1
            ip = "93.184.215.14" if calls["n"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        return real(host, port, *args, **kwargs)

    url_safety.socket.getaddrinfo = rebinding
    try:
        port = base.rsplit(":", 1)[1]
        a = ExternalHttpAgent("rebind", f"http://rebind.example:{port}/json")
        check("DNS rebinding refused at connect time", raises(lambda: a.respond("x"), ExternalAgentError, "loopback"))
        check("pre check and connect both resolved", calls["n"] >= 2, calls)
    finally:
        url_safety.socket.getaddrinfo = real
        config.EXTERNAL_AGENT_ALLOW_PRIVATE = True
    a = ExternalHttpAgent("private-off", f"{base}/json")
    config.EXTERNAL_AGENT_ALLOW_PRIVATE = False
    try:
        check("loopback refused when allow_private is off", raises(lambda: a.respond("x"), ExternalAgentError, "loopback"))
    finally:
        config.EXTERNAL_AGENT_ALLOW_PRIVATE = True


# 4. Admin API and service

def test_admin_api(base):
    print("admin service and API")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import event, select

    from admin import routes, service
    from db.base import Base, engine, session_scope
    from db.models import ExternalAgent

    Base.metadata.create_all(engine)

    app = FastAPI()
    app.include_router(routes.router)
    locked = TestClient(app)
    check("401 without an admin session", locked.get("/admin/api/agents").status_code == 401)
    check("401 on writes without a session", locked.post("/admin/api/agents/deactivate").status_code == 401)

    app.dependency_overrides[routes.require_admin] = lambda: {"sub": "admin", "username": "admin"}
    client = TestClient(app)

    good = {
        "name": "Stub", "url": f"{base}/json", "method": "POST",
        "auth_header_name": "X-Api-Key", "auth_header_value": "s3cret",
        "body_template": '{"message": "{text}"}', "response_path": "reply",
        "timeout_seconds": 5, "streaming": False,
    }
    r = client.post("/admin/api/agents", json=good)
    check("create agent 201", r.status_code == 201, r.text)
    created = r.json()
    check("auth value never returned", "s3cret" not in r.text and created["has_auth_header"] is True)
    with session_scope() as s:
        stored = s.get(ExternalAgent, created["id"]).auth_header_value_encrypted
    check("stored value encrypted", stored and "s3cret" not in stored and service.decrypt_secret(stored) == "s3cret")

    for label, patch, expected in [
        ("metadata URL rejected at save", {"url": "http://169.254.169.254/"}, "metadata"),
        ("credentials in URL rejected", {"url": "http://a:b@127.0.0.1/"}, "credentials"),
        ("template without {text}", {"body_template": '{"m": "x"}'}, "{text}"),
        ("template not JSON", {"body_template": '{"m": {text}}'}, "valid json"),
        ("reserved header", {"auth_header_name": "Host"}, "cannot be used"),
        ("header value line break", {"auth_header_value": "a\r\nX: y"}, "line breaks"),
        ("timeout out of range", {"timeout_seconds": 500}, "between"),
        ("duplicate name", {}, "already exists"),
    ]:
        r = client.post("/admin/api/agents", json={**good, **patch})
        check(f"{label} -> 400", r.status_code == 400 and expected.lower() in r.text.lower(), r.text)
    r = client.post("/admin/api/agents", json={**good, "method": "DELETE", "name": "x"})
    check("bad method -> 422", r.status_code == 422)

    r = client.post("/admin/api/agents", json={**good, "name": "Streamer", "url": f"{base}/sse", "response_path": "delta",
                                                "streaming": True, "auth_header_name": None, "auth_header_value": None})
    check("create second agent", r.status_code == 201, r.text)
    second = r.json()

    r = client.put(f"/admin/api/agents/{created['id']}", json={**good, "auth_header_value": "", "timeout_seconds": 7})
    check("update keeps secret when blank", r.status_code == 200 and r.json()["has_auth_header"] and r.json()["timeout_seconds"] == 7, r.text)
    check("update of missing id -> 404", client.put("/admin/api/agents/9999", json=good).status_code == 404)

    listing = client.get("/admin/api/agents").json()
    check("list returns both, none active", len(listing["agents"]) == 2 and listing["active_agent_id"] is None)
    check("list never includes the secret", "s3cret" not in json.dumps(listing))

    check("no active agent -> None", service.get_active_agent() is None)
    r = client.post(f"/admin/api/agents/{created['id']}/activate")
    check("activate", r.status_code == 200 and r.json()["is_active"])
    active = service.get_active_agent()
    check("get_active_agent returns it", active is not None and active.name == "Stub" and active.auth_header_value == "s3cret")
    check("active agent answers", active.respond("hi") == "You said: hi")

    statements = {"n": 0}

    def count(*args):
        statements["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    start = time.perf_counter()
    for _ in range(1000):
        service.get_active_agent()
    per_call_us = (time.perf_counter() - start) * 1000
    event.remove(engine, "before_cursor_execute", count)
    check(f"cached get_active_agent: no SQL, {per_call_us:.3f} us per call", statements["n"] == 0 and per_call_us < 50, statements)
    check("summary", service.active_agent_summary() == {"id": created["id"], "name": "Stub", "usable": True})

    client.post(f"/admin/api/agents/{second['id']}/activate")
    with session_scope() as s:
        active_rows = s.scalars(select(ExternalAgent).where(ExternalAgent.is_active.is_(True))).all()
        check("exactly one active after switching", [row.id for row in active_rows] == [second["id"]])
    check("cache invalidated on activate", service.get_active_agent().name == "Streamer")
    check("activate missing -> 404", client.post("/admin/api/agents/9999/activate").status_code == 404)

    r = client.post(f"/admin/api/agents/{created['id']}/test", json={"message": "ping"})
    body = r.json()
    check("test saved agent", r.status_code == 200 and body["ok"] and body["reply"] == "You said: ping" and body["latency_ms"] >= 0, body)
    r = client.post(f"/admin/api/agents/{second['id']}/test")
    check("test streaming agent without body", r.json()["ok"] and r.json()["reply"] == "First. Second. Third.", r.text)
    r = client.post("/admin/api/agents/test", json={**good, "url": f"{base}/secured", "auth_header_value": "", "id": created["id"]})
    check("test unsaved edits uses stored secret", r.json()["ok"] and r.json()["reply"] == "authorized", r.text)
    r = client.post("/admin/api/agents/test", json={**good, "name": "Draft", "url": f"{base}/secured", "auth_header_name": None, "auth_header_value": None})
    check("test unsaved config reports failure", r.status_code == 200 and not r.json()["ok"] and "401" in r.json()["error"], r.text)
    r = client.post("/admin/api/agents/test", json={**good, "url": "http://169.254.169.254/"})
    check("test with SSRF URL reports refusal", not r.json()["ok"] and "metadata" in r.json()["error"], r.text)
    check("test missing id -> 404", client.post("/admin/api/agents/9999/test").status_code == 404)

    r = client.post("/admin/api/agents/deactivate")
    check("deactivate all", r.status_code == 200 and service.get_active_agent() is None and service.active_agent_summary() is None)
    client.post(f"/admin/api/agents/{created['id']}/activate")
    r = client.delete(f"/admin/api/agents/{created['id']}")
    check("delete active agent -> built in", r.status_code == 200 and service.get_active_agent() is None)
    check("delete missing -> 404", client.delete(f"/admin/api/agents/{created['id']}").status_code == 404)

    # MCP clients (Agent D's mcp_server.clients)
    try:
        import mcp_server.clients  # noqa: F401
        have_mcp = True
    except ImportError as exc:
        have_mcp = False
        print(f"        mcp_server.clients not importable yet ({exc}); MCP endpoints expect 503")
    r = client.post("/admin/api/mcp-clients", json={"name": "Desk bot", "scopes": ["knowledge:read"]})
    if have_mcp:
        check("create MCP client returns secret once", r.status_code == 201 and r.json()["client_secret"] and r.headers.get("cache-control") == "no-store", r.text)
        pk = r.json()["client"]["id"]
        listing = client.get("/admin/api/mcp-clients").json()
        check("list MCP clients has no secret", len(listing) == 1 and "client_secret" not in listing[0] and "secret_hash" not in json.dumps(listing))
        check("bad scope -> 400", client.post("/admin/api/mcp-clients", json={"name": "x", "scopes": ["root"]}).status_code == 400)
        check("revoke", client.delete(f"/admin/api/mcp-clients/{pk}").status_code == 200)
        check("revoked shows inactive", client.get("/admin/api/mcp-clients").json()[0]["active"] is False)
        check("revoke missing -> 404", client.delete("/admin/api/mcp-clients/9999").status_code == 404)
        audit = client.get("/admin/api/audit?limit=20")
        actions = [row["action"] for row in audit.json()]
        check("audit lists admin actions", audit.status_code == 200 and "agent.create" in actions and "mcp_client.revoke" in actions, actions)
        check("audit limit validated", client.get("/admin/api/audit?limit=0").status_code == 422)
    else:
        check("MCP endpoints 503 without mcp_server", r.status_code == 503, r.text)


def main():
    test_url_safety()
    test_helpers()
    server, base = start_stub()
    try:
        test_live_agent(base)
        test_admin_api(base)
    finally:
        server.shutdown()
    print(f"\n{RESULTS['passed']} passed, {RESULTS['failed']} failed (temp dir {_TMP})")
    return 1 if RESULTS["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
