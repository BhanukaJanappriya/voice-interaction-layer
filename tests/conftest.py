"""Shared test setup.

The environment is configured here, at import time, BEFORE anything from
backend/ is imported, because config.py reads it once on import. Every
value the app could otherwise pick up from a developer's .env file is set
explicitly (even to an empty string), since load_dotenv() never overrides
a variable that is already set.

Nothing in the non live suite needs audio hardware, a model, a cloud key
or the network: speech in and out is replaced inside server.py's own
namespace, and each test that reaches an LLM replaces the HTTP call.

Features other workstreams are still building are detected, not assumed:
a test for a module or endpoint that does not exist yet skips with a
reason, and starts running by itself once the feature lands.
"""

import base64
import inspect
import itertools
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"

_TMP_DIR = Path(tempfile.mkdtemp(prefix="voice-assistant-tests-"))

TEST_ENV = {
    "DATA_DIR": str(_TMP_DIR),
    "DATABASE_URL": f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}",
    "KNOWLEDGE_DIR": str(_TMP_DIR / "knowledge"),
    "JWT_SECRET": "test-jwt-secret-not-for-production-0123456789abcdef",
    "FIELD_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
    "WARM_UP_ON_STARTUP": "false",
    "PUBLIC_BASE_URL": "http://127.0.0.1:8000",
    "COOKIE_SECURE": "false",
    "ADMIN_USERNAME": "envadmin",
    "ADMIN_PASSWORD": "env-admin-password-123",
    "EXTERNAL_AGENT_ALLOW_PRIVATE": "false",
    "EXTERNAL_AGENT_MAX_RESPONSE_BYTES": "1000000",
    "EXTERNAL_AGENT_DEFAULT_TIMEOUT": "5",
    # Settings from a developer's real .env must never reach the suite: a test
    # that built a default OTP service could otherwise ask the real MCP hub to
    # send a real SMS. Blanked or pinned here, tests that need one set it.
    # Empty keeps the older single provider path that the existing orchestrator
    # tests describe; tests of the fallback chain set LLM_CHAIN themselves.
    "LLM_CHAIN": "",
    "MCP_HUB_API_KEY": "",
    "MCP_HUB_CONTEXT_BUDGET_SECONDS": "5",
    "MCP_HUB_IN_REPLIES": "true",
    "OTP_BACKEND": "local",
    "OTP_SENDER": "console",
    "SMS_GATEWAY_URL": "",
    "SMS_GATEWAY_API_KEY": "",
    "SMTP_HOST": "",
    "LIVEKIT_URL": "",
    "LIVEKIT_API_KEY": "",
    "LIVEKIT_API_SECRET": "",
    "VOICE_TRANSPORT": "websocket",
    "AVATAR_RENDERER": "legacy",
    "AUTH_METHODS": "pin",
    "VOICEKIT_API_KEYS": "",
    "GEMINI_API_KEY": "",
    "OPENAI_API_KEY": "",
    "CLOUD_LLM_PROVIDER": "gemini",
    "CLOUD_REASONING_STAGE": "false",
    "GOOGLE_APPLICATION_CREDENTIALS": "",
    "AZURE_SPEECH_KEY": "",
    "AZURE_SPEECH_REGION": "",
    "LOCAL_LLM_HOST": "http://127.0.0.1:11434",
    "LOGIN_MAX_ATTEMPTS": "5",
    "LOGIN_LOCKOUT_MINUTES": "15",
    "LOGIN_RATE_LIMIT_PER_MINUTE": "10",
    "USER_SESSION_MINUTES": "60",
    "ADMIN_SESSION_MINUTES": "60",
    "MCP_TOKEN_MINUTES": "15",
    "KNOWLEDGE_ENABLED": "true",
    "MCP_ENABLED": "true",
}
os.environ.update(TEST_ENV)

# bcrypt at its production cost (12 rounds) makes every login in the
# suite take a quarter of a second; 4 rounds keeps the same code path.
import bcrypt  # noqa: E402

_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _real_gensalt(4, prefix)

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BASE_URL = "http://127.0.0.1:8000"
USER_PHONE_RAW = "011 234 5678"
USER_PHONE_E164 = "+94112345678"
USER_PIN = "4321"
USER_NAME = "Nimal Perera"
ADMIN_USERNAME = "qa-admin"
ADMIN_PASSWORD = "qa-admin-password-456"


# Feature detection helpers


def optional_import(name):
    """The module, or None when it has not been delivered yet."""
    try:
        return __import__(name, fromlist=["_"])
    except ModuleNotFoundError as exc:
        # Only "this module is missing" means not delivered; a missing
        # dependency inside a delivered module is a real failure.
        missing = exc.name or ""
        if name == missing or name.startswith(missing + "."):
            return None
        raise


def require_module(name, reason=None):
    module = optional_import(name)
    if module is None:
        pytest.skip(reason or f"module {name} not delivered yet")
    return module


def app_paths(app):
    """Every path the app serves. FastAPI 0.141 keeps an included router
    as one nested _IncludedRouter entry, so walk into those."""
    paths = set()

    def _collect(routes, prefix):
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                context = getattr(route, "include_context", None)
                _collect(nested.routes, prefix + (getattr(context, "prefix", "") or ""))
                continue
            path = getattr(route, "path", None)
            if path is not None:
                paths.add(prefix + path)

    _collect(app.routes, "")
    return paths


def server_source():
    import server

    return inspect.getsource(server)


def orchestrator_integrated():
    """True once the lead has wired the plan's Orchestrator changes
    (external_agent_provider, context in the local system prompt)."""
    import agent

    return "external_agent_provider" in inspect.signature(agent.Orchestrator.__init__).parameters


def items_of(payload):
    """A list endpoint may return a bare list or {"something": [...]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                return value
    return []


def id_of(payload):
    if isinstance(payload, dict):
        if "id" in payload:
            return payload["id"]
        for value in payload.values():
            if isinstance(value, dict) and "id" in value:
                return value["id"]
    raise AssertionError(f"no id in response {payload!r}")


# Database


@pytest.fixture(scope="session")
def db_schema():
    import config  # noqa: F401  (reads the environment set above)
    from db import base, models  # noqa: F401

    migrate = optional_import("db.migrate")
    if migrate is not None and hasattr(migrate, "upgrade_to_head"):
        migrate.upgrade_to_head()
    else:
        base.Base.metadata.create_all(base.engine)
    yield base
    base.engine.dispose()


def _reset_service_caches():
    service = optional_import("admin.service")
    if service is None:
        return
    for name in ("invalidate_cache", "_invalidate_cache", "clear_cache", "_invalidate", "invalidate"):
        func = getattr(service, name, None)
        if callable(func):
            try:
                func()
            except TypeError:
                pass


@pytest.fixture(autouse=True)
def clean_db(db_schema):
    """Every test starts with empty tables."""
    from db import models

    def _wipe():
        with db_schema.session_scope() as session:
            for model in (models.AuditLog, models.McpClient, models.ExternalAgent, models.User, models.Admin):
                session.query(model).delete()
        _reset_service_caches()

    _wipe()
    yield
    _wipe()


@pytest.fixture
def make_user(db_schema):
    from auth.passwords import hash_secret
    from db import models

    def _make(phone_e164=USER_PHONE_E164, display_name=USER_NAME, pin=USER_PIN, language=None):
        with db_schema.session_scope() as session:
            user = models.User(
                phone_e164=phone_e164,
                display_name=display_name,
                pin_hash=hash_secret(pin),
                preferred_language=language,
                failed_attempts=0,
            )
            session.add(user)
            session.flush()
            return user.id

    return _make


@pytest.fixture
def make_admin(db_schema):
    from auth.passwords import hash_secret
    from db import models

    def _make(username=ADMIN_USERNAME, password=ADMIN_PASSWORD):
        with db_schema.session_scope() as session:
            admin = models.Admin(username=username, password_hash=hash_secret(password), failed_attempts=0)
            session.add(admin)
            session.flush()
            return admin.id

    return _make


# The server, with speech in and out replaced


class _FakeVad:
    def __init__(self, *args, **kwargs):
        pass

    def calibrate_noise_floor(self, sample_audio):
        return 0.0

    def is_speech(self, audio_chunk):
        return True


class FakeSpeech:
    """Scripted transcripts for server.transcribe_stream; records what the
    fake TTS was asked to say."""

    def __init__(self):
        self.transcripts = []
        self.spoken = []

    def transcribe_stream(self, audio_chunk_iterator, language_hint=None, vad=None):
        for _chunk in audio_chunk_iterator:
            if self.transcripts:
                text = self.transcripts.pop(0)
                yield types.SimpleNamespace(text=text, is_final=True, language=language_hint or "en")
                return

    def speak_stream(self, sentences, language="en", voice_style="female_natural", on_sentence_start=None):
        for sentence in sentences:
            self.spoken.append(sentence)
            if on_sentence_start is not None:
                on_sentence_start(sentence)

    def speak_text(self, text_input, language="en", voice_style="female_natural", on_sentence_start=None):
        self.spoken.append(text_input)
        if on_sentence_start is not None:
            on_sentence_start(text_input)


@pytest.fixture(scope="session")
def fake_speech_holder():
    return {"current": FakeSpeech()}


@pytest.fixture(scope="session")
def server_module(db_schema, fake_speech_holder):
    patcher = pytest.MonkeyPatch()
    import agent
    import server

    holder = fake_speech_holder
    patcher.setattr(server, "transcribe_stream", lambda *a, **k: holder["current"].transcribe_stream(*a, **k))
    patcher.setattr(server, "speak_stream", lambda *a, **k: holder["current"].speak_stream(*a, **k))
    patcher.setattr(server, "speak_text", lambda *a, **k: holder["current"].speak_text(*a, **k))
    patcher.setattr(server, "VoiceActivityDetector", _FakeVad)
    for name in ("_warm_up", "precache_speech", "warm_up_stt", "warm_up_tts"):
        if hasattr(server, name):
            patcher.setattr(server, name, lambda *a, **k: None)
    patcher.setattr(agent.LlamaOllamaAgent, "warm_up", lambda self: None)
    yield server
    patcher.undo()


@pytest.fixture
def fake_speech(fake_speech_holder):
    fake = FakeSpeech()
    fake_speech_holder["current"] = fake
    return fake


_ip_counter = itertools.count(1)


def _unique_client_address():
    """Each client gets its own address, so the per address login rate
    limit of one test never leaks into another."""
    n = next(_ip_counter)
    return (f"10.77.{n // 250}.{n % 250 + 1}", 50000)


def _find_router(module_names):
    for module_name in module_names:
        module = optional_import(module_name)
        if module is None:
            continue
        router = getattr(module, "router", None)
        if router is not None:
            return router
    return None


@pytest.fixture(scope="session")
def api_app(server_module):
    """server.app once the lead has integrated the routers. Before that,
    a bare FastAPI app with whatever routers the workstreams delivered, so
    their endpoints are tested as soon as they exist."""
    app = server_module.app
    if "/auth/verify" in app_paths(app):
        return app
    from fastapi import FastAPI

    fallback = FastAPI()
    found = False
    for candidates in (
        ("auth.routes",),
        ("admin.routes", "admin.api", "admin.router"),
    ):
        router = _find_router(candidates)
        if router is not None:
            fallback.include_router(router)
            found = True
    if not found:
        return app
    return fallback


def make_client(app, **kwargs):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url=BASE_URL, client=_unique_client_address(), **kwargs)


@pytest.fixture
def client(api_app):
    """A TestClient without the app lifespan (no warm up, no MCP session
    manager); enough for plain HTTP and WebSocket routes."""
    with_client = make_client(api_app)
    yield with_client
    with_client.close()


@pytest.fixture
def new_client(api_app):
    """Factory for extra clients (fresh cookie jar, fresh address)."""
    made = []

    def _new():
        c = make_client(api_app)
        made.append(c)
        return c

    yield _new
    for c in made:
        c.close()


def require_path(app, path):
    if path not in app_paths(app):
        pytest.skip(f"{path} is not served yet (feature not delivered or not integrated)")


@pytest.fixture
def login_user(make_user):
    """Creates the standard user and logs in with the given client;
    returns the /auth/verify JSON."""

    def _login(client, phone=USER_PHONE_RAW, pin=USER_PIN):
        require_path(client.app, "/auth/verify")
        make_user()
        response = client.post("/auth/verify", json={"phone": phone, "pin": pin})
        assert response.status_code == 200, response.text
        return response.json()

    return _login


@pytest.fixture
def admin_client(api_app, make_admin):
    require_path(api_app, "/auth/admin/login")
    make_admin()
    c = make_client(api_app)
    response = c.post("/auth/admin/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    yield c
    c.close()


# A local HTTP stub for external agent tests


class StubState:
    def __init__(self):
        self.requests = []  # (path, headers dict, body bytes)
        self.lock = threading.Lock()

    def hits(self, path):
        with self.lock:
            return sum(1 for p, _, _ in self.requests if p == path)

    def last(self, path):
        with self.lock:
            for p, headers, body in reversed(self.requests):
                if p == path:
                    return headers, body
        return None, None


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _send(self, status, body, content_type="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._handle(b"")

        def do_POST(self):
            self._handle(self._body())

        def do_PUT(self):
            self._handle(self._body())

        def _handle(self, body):
            path = self.path.split("?")[0]
            with state.lock:
                state.requests.append((path, {k.lower(): v for k, v in self.headers.items()}, body))
            if path == "/echo":
                try:
                    parsed = json.loads(body or b"{}")
                except ValueError:
                    return self._send(400, {"error": "bad json"})
                return self._send(200, {"reply": f"echo: {parsed.get('message', '')}", "received": parsed})
            if path == "/nested":
                return self._send(200, {"choices": [{"message": {"content": "from the list"}}]})
            if path == "/missing":
                return self._send(200, {"something_else": "x"})
            if path == "/notjson":
                return self._send(200, "this is not json", content_type="text/plain")
            if path == "/big":
                return self._send(200, {"reply": "x" * 200_000})
            if path == "/slow":
                time.sleep(1.5)
                return self._send(200, {"reply": "too late"})
            if path == "/fail":
                return self._send(500, {"error": "boom"})
            if path == "/notfound":
                return self._send(404, {"error": "nope"})
            if path == "/redirect":
                return self._send(302, b"", extra={"Location": "/echo"})
            if path in ("/sse", "/sse-json"):
                if path == "/sse":
                    events = ["data: Hel\n\n", "data: lo\n\n"]
                else:
                    events = ['data: {"delta": "Hello"}\n\n', 'data: {"delta": " world"}\n\n']
                payload = "".join(events).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                for event in events:
                    self.wfile.write(event.encode("utf-8"))
                    self.wfile.flush()
                return
            return self._send(404, {"error": "unknown stub path"})

    return Handler


@pytest.fixture(scope="session")
def stub_server():
    """A tiny HTTP server on 127.0.0.1 and an ephemeral port, standing in
    for an external chat agent."""
    state = StubState()
    class _QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # the size cap and timeout tests hang up on purpose

    httpd = _QuietServer(("127.0.0.1", 0), _make_handler(state))
    httpd.daemon_threads = True
    httpd.block_on_close = False
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    stub = types.SimpleNamespace(url=f"http://127.0.0.1:{port}", state=state)
    yield stub
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def allow_private(monkeypatch):
    """Lets outbound calls reach the 127.0.0.1 stub. Also covers a
    check_outbound_url whose allow_private default was bound at import
    time; test_url_safety has a dedicated test that exposes that case."""
    import config

    monkeypatch.setattr(config, "EXTERNAL_AGENT_ALLOW_PRIVATE", True)
    url_safety = optional_import("url_safety")
    if url_safety is not None:
        func = getattr(url_safety, "check_outbound_url", None)
        if func is not None and func.__defaults__:
            monkeypatch.setattr(func, "__defaults__", tuple(True for _ in func.__defaults__))
        if func is not None and func.__kwdefaults__ and "allow_private" in func.__kwdefaults__:
            monkeypatch.setattr(func, "__kwdefaults__", {**func.__kwdefaults__, "allow_private": True})
    return True


# A fake Ollama for the local path


class FakeOllamaResponse:
    def __init__(self, lines, status_code=200):
        self._lines = [json.dumps(line).encode("utf-8") for line in lines]
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(response=self)

    def iter_lines(self):
        return iter(self._lines)

    def json(self):
        text = "".join(json.loads(line).get("response", "") for line in self._lines)
        return {"response": text, "done": True}


@pytest.fixture
def fake_ollama(monkeypatch):
    """Replaces requests.post inside agent.py. The reply is produced by
    the test's own function from the request payload; every payload is
    recorded."""
    import agent

    recorded = []
    state = {"reply": lambda payload: "Local reply."}

    def _post(url, json=None, **kwargs):
        recorded.append(json or {})
        text = state["reply"](json or {})
        return FakeOllamaResponse([{"response": text, "done": False}, {"response": "", "done": True}])

    monkeypatch.setattr(agent.requests, "post", _post)
    return types.SimpleNamespace(payloads=recorded, state=state)


def pytest_sessionfinish(session, exitstatus):
    try:
        from db import base

        base.engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
