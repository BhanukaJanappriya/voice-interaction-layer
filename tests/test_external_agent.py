"""agent.ExternalHttpAgent against a local stub HTTP server."""

import json
import time

import pytest

import agent
import config


@pytest.fixture(autouse=True)
def _require_external_agent():
    if not hasattr(agent, "ExternalHttpAgent") or not hasattr(agent, "ExternalAgentError"):
        pytest.skip("agent.ExternalHttpAgent not delivered yet (workstream B)")


def _agent(stub, path, **kwargs):
    kwargs.setdefault("name", "Stub")
    return agent.ExternalHttpAgent(url=f"{stub.url}{path}", **kwargs)


def test_is_a_chat_agent():
    assert issubclass(agent.ExternalHttpAgent, agent.ChatAgent)
    assert issubclass(agent.ExternalAgentError, Exception)


def test_plain_reply_and_auth_header(stub_server, allow_private):
    external = _agent(stub_server, "/echo", auth_header_name="X-Api-Key", auth_header_value="s3cret-value")
    reply = external.respond("hello there")
    assert reply == "echo: hello there"
    headers, _ = stub_server.state.last("/echo")
    assert headers.get("x-api-key") == "s3cret-value"


def test_body_template_escapes_quotes_newlines_and_backslashes(stub_server, allow_private):
    text = 'He said "hi"\nthen left \\ quickly\t{braces}'
    name = 'O"Brien'
    external = _agent(
        stub_server,
        "/echo",
        body_template='{"message": "{text}", "user": "{user_name}", "lang": "{language}"}',
    )
    external.respond(text, user_name=name, language="si")
    _, body = stub_server.state.last("/echo")
    parsed = json.loads(body)
    assert parsed["message"] == text
    assert parsed["user"] == name
    assert parsed["lang"] == "si"


def test_response_path_with_list_index(stub_server, allow_private):
    external = _agent(stub_server, "/nested", response_path="choices.0.message.content")
    assert external.respond("hi") == "from the list"


def test_missing_response_path_raises(stub_server, allow_private):
    external = _agent(stub_server, "/missing", response_path="reply")
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")


def test_non_json_response_raises(stub_server, allow_private):
    external = _agent(stub_server, "/notjson", response_path="reply")
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")


def test_respond_stream_non_streaming_yields_reply(stub_server, allow_private):
    external = _agent(stub_server, "/echo")
    assert "".join(external.respond_stream("abc")) == "echo: abc"


def test_sse_plain_text_events(stub_server, allow_private):
    external = _agent(stub_server, "/sse", streaming=True)
    pieces = list(external.respond_stream("hi"))
    assert "".join(pieces) == "Hello"


def test_sse_json_events_use_response_path(stub_server, allow_private):
    external = _agent(stub_server, "/sse-json", streaming=True, response_path="delta")
    pieces = list(external.respond_stream("hi"))
    assert "".join(pieces) == "Hello world"
    assert len(pieces) >= 2, "SSE pieces should be yielded as they arrive, not joined first"


def test_response_size_cap(stub_server, allow_private, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_AGENT_MAX_RESPONSE_BYTES", 10_000)
    external = _agent(stub_server, "/big")
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")


def test_timeout(stub_server, allow_private):
    external = _agent(stub_server, "/slow", timeout_seconds=0.3)
    start = time.monotonic()
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")
    assert time.monotonic() - start < 1.4


@pytest.mark.parametrize("path", ["/fail", "/notfound"])
def test_non_2xx_raises(stub_server, allow_private, path):
    external = _agent(stub_server, path)
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")


def test_redirect_not_followed(stub_server, allow_private):
    before = stub_server.state.hits("/echo")
    external = _agent(stub_server, "/redirect")
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")
    assert stub_server.state.hits("/echo") == before


def test_network_error_raises(allow_private):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    external = agent.ExternalHttpAgent(name="Closed", url=f"http://127.0.0.1:{port}/chat", timeout_seconds=2)
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")


def test_private_address_refused_without_flag(stub_server, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_AGENT_ALLOW_PRIVATE", False)
    before = stub_server.state.hits("/echo")
    external = _agent(stub_server, "/echo")
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")
    with pytest.raises(agent.ExternalAgentError):
        list(external.respond_stream("hi"))
    assert stub_server.state.hits("/echo") == before


def test_dns_rebinding_to_private_address_refused_at_connect(stub_server, monkeypatch):
    """The name resolves to a public address when the URL is checked and
    to loopback when the connection is opened; the connection must still
    be refused."""
    import socket

    monkeypatch.setattr(config, "EXTERNAL_AGENT_ALLOW_PRIVATE", False)
    port = int(stub_server.url.rsplit(":", 1)[1])
    real = socket.getaddrinfo
    answers = {"count": 0}

    def _getaddrinfo(host, port_arg, *args, **kwargs):
        if host == "rebind.example.com":
            answers["count"] += 1
            address = "93.184.216.34" if answers["count"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port_arg or port))]
        return real(host, port_arg, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    before = stub_server.state.hits("/echo")
    external = agent.ExternalHttpAgent(name="Rebind", url=f"http://rebind.example.com:{port}/echo", timeout_seconds=2)
    with pytest.raises(agent.ExternalAgentError):
        external.respond("hi")
    assert stub_server.state.hits("/echo") == before
