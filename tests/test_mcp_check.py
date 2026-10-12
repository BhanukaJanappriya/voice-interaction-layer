"""python -m backend.mcp_client.check against local stand ins: a working SSE
hub, 401 and 403 hubs, streamable only and SSE only hubs, a wrong path, a
hub that never answers, a closed port, and the promise that the key is never
printed. No real hub is contacted."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import config
from conftest import require_module

KEY = "check-test-key-SECRET-abcdef123456"


@pytest.fixture(scope="module")
def mods():
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    return type(
        "Mods",
        (),
        dict(
            check=require_module("mcp_client.check", "mcp_client.check not delivered yet"),
            mock=require_module("mcp_client.mock_hub"),
        ),
    )


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(config, "MCP_HUB_API_KEY", KEY)
    monkeypatch.setattr(config, "MCP_HUB_AUTH_HEADER", "X-API-Key")
    monkeypatch.setattr(config, "MCP_HUB_ENABLED", True)


class _Stub:
    """An HTTP server that answers every request with one status."""

    def __init__(self, status=404, delay=0.0, headers=None):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                if stub.delay:
                    time.sleep(stub.delay)
                body = b"stub"
                try:
                    self.send_response(stub.status)
                    for k, v in stub.headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, *args):
                pass

        self.status, self.delay, self.headers = status, delay, headers or {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run(mods, capsys, url, *flags, timeout="1.5"):
    code = mods.check.main(["--url", url, "--timeout", timeout, "--json", *flags])
    out = capsys.readouterr().out
    return code, json.loads(out), out


@pytest.fixture
def sse_hub(mods):
    hub = mods.mock.MockHub(mode="sse", api_key=KEY)
    hub.start()
    yield hub
    hub.stop()


def test_working_sse_hub(mods, sse_hub, capsys):
    code, report, out = run(mods, capsys, sse_hub.url)
    assert code == 0
    assert report["verdict"]["code"] == "working"
    assert report["hub_client"]["transport"] == "sse"
    assert report["auth"] == "accepted"
    assert report["probes"]["sse"]["status"] == 200
    assert report["probes"]["sse"]["first_event"] == "endpoint"
    assert report["probes"]["without_key"]["status"] == 401
    names = {t["name"] for t in report["hub_client"]["tools"]}
    assert "search_knowledgebase_tool" in names
    assert KEY not in out


def test_text_output_lists_tools_and_never_the_key(mods, sse_hub, capsys):
    code = mods.check.main(["--url", sse_hub.url, "--timeout", "1.5"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Verdict: working" in out
    assert "search_knowledgebase_tool" in out
    assert "X-API-Key" in out and f"length {len(KEY)}" in out
    assert KEY not in out


def test_streamable_only_hub_is_found_too(mods, capsys):
    hub = mods.mock.MockHub(mode="streamable", path="/mcp", api_key=KEY)
    hub.start()
    try:
        code, report, out = run(mods, capsys, hub.url)
    finally:
        hub.stop()
    assert code == 0
    assert report["hub_client"]["transport"] == "streamable-http"
    assert report["probes"]["streamable_mcp"]["status"] in (200, 202)
    assert report["probes"]["sse"]["status"] == 404
    assert KEY not in out


def test_wrong_key_is_auth_rejected_401(mods, capsys):
    hub = mods.mock.MockHub(mode="sse", api_key="a-different-key-entirely")
    hub.start()
    try:
        code, report, out = run(mods, capsys, hub.url)
    finally:
        hub.stop()
    assert code == 1
    assert report["verdict"]["code"] == "auth_rejected"
    assert report["auth"] == "rejected 401"
    assert "administrator" in report["verdict"]["ask_the_administrator"]
    assert KEY not in out


def test_403_is_auth_rejected(mods, capsys):
    stub = _Stub(status=403)
    try:
        code, report, out = run(mods, capsys, stub.url)
    finally:
        stub.close()
    assert code == 1
    assert report["verdict"]["code"] == "auth_rejected"
    assert report["auth"] == "rejected 403"
    assert KEY not in out


def test_401_with_www_authenticate_header_name_is_reported(mods, capsys):
    stub = _Stub(status=401, headers={"WWW-Authenticate": "Bearer realm=hub"})
    try:
        code, report, out = run(mods, capsys, stub.url)
    finally:
        stub.close()
    assert code == 1
    assert "www-authenticate" in report["probes"]["sse"]["headers"]
    assert report["auth"] == "rejected 401"


def test_wrong_path_all_404(mods, capsys):
    stub = _Stub(status=404)
    try:
        code, report, out = run(mods, capsys, stub.url)
    finally:
        stub.close()
    assert code == 1
    assert report["verdict"]["code"] == "wrong_path"
    assert "404" in report["verdict"]["summary"]
    assert KEY not in out


def test_wrong_transport_streamable_server_sitting_at_the_sse_path(mods, capsys):
    hub = mods.mock.MockHub(mode="streamable", path="/sse", api_key=KEY)
    hub.start()
    try:
        code, report, out = run(mods, capsys, hub.url)
    finally:
        hub.stop()
    assert code == 1
    assert report["probes"]["sse"]["status"] not in (404, 401, 403, None)
    assert report["verdict"]["code"] == "wrong_transport"
    assert KEY not in out


def test_wrong_transport_405_everywhere(mods, capsys):
    stub = _Stub(status=405, headers={"Allow": "GET, HEAD"})
    try:
        code, report, _ = run(mods, capsys, stub.url)
    finally:
        stub.close()
    assert code == 1
    assert report["verdict"]["code"] == "wrong_transport"
    assert report["probes"]["sse"]["headers"]["allow"] == "GET, HEAD"


def test_server_error_is_unreachable(mods, capsys):
    stub = _Stub(status=503)
    try:
        code, report, _ = run(mods, capsys, stub.url)
    finally:
        stub.close()
    assert code == 1
    assert report["verdict"]["code"] == "unreachable"


def test_timeout_is_unreachable(mods, capsys):
    stub = _Stub(status=200, delay=4.0)
    try:
        started = time.monotonic()
        code, report, out = run(mods, capsys, stub.url, timeout="0.5")
        elapsed = time.monotonic() - started
    finally:
        stub.close()
    assert code == 1
    assert report["verdict"]["code"] == "unreachable"
    assert "timed out" in report["verdict"]["summary"]
    assert elapsed < 12
    assert KEY not in out


def test_closed_port_is_unreachable(mods, capsys):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    code, report, out = run(mods, capsys, f"http://127.0.0.1:{port}")
    assert code == 1
    assert report["verdict"]["code"] == "unreachable"
    assert report["network"]["tcp"]["ok"] is False
    assert KEY not in out


def test_dns_failure_is_unreachable(mods, capsys):
    code, report, _ = run(mods, capsys, "http://no-such-host.invalid")
    assert code == 1
    assert report["verdict"]["code"] == "unreachable"
    assert report["network"]["dns"]["ok"] is False


def test_no_key_is_not_configured(mods, sse_hub, capsys, monkeypatch):
    monkeypatch.setattr(config, "MCP_HUB_API_KEY", "")
    code, report, _ = run(mods, capsys, sse_hub.url)
    assert code == 1
    assert report["verdict"]["code"] == "not_configured"
    assert report["config"]["key_set"] is False


def test_read_only_flag_calls_only_the_allowed_tools_once(mods, sse_hub, capsys):
    sse_hub.reset_counters()
    code, report, _ = run(mods, capsys, sse_hub.url, "--call-read-only")
    assert code == 0
    called = [name for name, _args in sse_hub.tool_calls]
    assert called == ["search_knowledgebase_tool"]  # the mock lists no other allowed tool
    statuses = {r["tool"]: r["status"] for r in report["read_only_calls"]}
    assert statuses["search_knowledgebase_tool"] == "ok"
    assert statuses["compare_package"] == "not listed by the hub"


def test_no_calls_without_the_flag(mods, sse_hub, capsys):
    sse_hub.reset_counters()
    run(mods, capsys, sse_hub.url)
    assert sse_hub.tool_calls == []


def test_read_only_list_is_fixed(mods):
    assert mods.check.READ_ONLY_TOOLS == ("search_knowledgebase_tool", "compare_package", "get_function_latencies")


def test_classify_is_pure(mods):
    base = {
        "config": {"key_set": True, "enabled": True},
        "network": {"host": "h", "port": 443, "dns": {"ok": True}, "tcp": {"ok": True}, "tls": {"ok": False, "error": "SSLError"}},
        "probes": {},
        "hub_client": {"ok": False, "error": "x"},
        "auth": "unknown",
    }
    code, summary, _ask = mods.check.classify(base)
    assert code == "unreachable" and "TLS" in summary
