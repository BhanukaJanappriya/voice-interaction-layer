"""/admin/api: external agent CRUD, activation, test connection, MCP
clients. Authentication failures are covered in test_auth_api.py."""

import pytest

from conftest import id_of, items_of, require_path

SECRET_HEADER_VALUE = "Bearer very-secret-partner-key-8841"


@pytest.fixture(autouse=True)
def _require_admin_api(api_app):
    require_path(api_app, "/admin/api/agents")


def _agent_body(stub_url, name="Partner", **overrides):
    body = {
        "name": name,
        "url": f"{stub_url}/echo",
        "method": "POST",
        "auth_header_name": "Authorization",
        "auth_header_value": SECRET_HEADER_VALUE,
        "body_template": '{"message": "{text}"}',
        "response_path": "reply",
        "timeout_seconds": 5,
        "streaming": False,
    }
    body.update(overrides)
    return body


def _create(admin_client, body):
    response = admin_client.post("/admin/api/agents", json=body)
    assert response.status_code in (200, 201), response.text
    assert SECRET_HEADER_VALUE not in response.text
    return id_of(response.json())


def _list(admin_client):
    response = admin_client.get("/admin/api/agents")
    assert response.status_code == 200, response.text
    assert SECRET_HEADER_VALUE not in response.text
    return items_of(response.json())


@pytest.fixture
def cleanup_active(admin_client):
    yield
    admin_client.post("/admin/api/agents/deactivate")


def test_create_list_update_delete(admin_client, stub_server, allow_private, cleanup_active):
    agent_id = _create(admin_client, _agent_body(stub_server.url))
    agents = _list(admin_client)
    assert [a["name"] for a in agents] == ["Partner"]
    listed = agents[0]
    # Only "set / not set" is shown for the credential.
    assert "auth_header_value" not in listed or listed["auth_header_value"] in (None, "", True, False)
    assert "auth_header_value_encrypted" not in listed

    response = admin_client.put(
        f"/admin/api/agents/{agent_id}", json=_agent_body(stub_server.url, name="Partner Renamed", auth_header_value=None)
    )
    assert response.status_code == 200, response.text
    assert SECRET_HEADER_VALUE not in response.text
    assert [a["name"] for a in _list(admin_client)] == ["Partner Renamed"]

    response = admin_client.delete(f"/admin/api/agents/{agent_id}")
    assert response.status_code in (200, 204)
    assert _list(admin_client) == []


def test_credential_stored_encrypted(admin_client, stub_server, allow_private, db_schema):
    from db.models import ExternalAgent

    _create(admin_client, _agent_body(stub_server.url))
    with db_schema.session_scope() as session:
        row = session.query(ExternalAgent).one()
        assert row.auth_header_value_encrypted
        assert SECRET_HEADER_VALUE not in row.auth_header_value_encrypted


def test_invalid_agent_rejected(admin_client, stub_server, allow_private):
    for bad in (
        _agent_body(stub_server.url, url="ftp://example.com/x"),
        _agent_body(stub_server.url, body_template="not json {text"),
        _agent_body(stub_server.url, name=""),
    ):
        response = admin_client.post("/admin/api/agents", json=bad)
        assert response.status_code in (400, 422), (bad, response.status_code, response.text)


def test_duplicate_name_rejected(admin_client, stub_server, allow_private):
    _create(admin_client, _agent_body(stub_server.url))
    response = admin_client.post("/admin/api/agents", json=_agent_body(stub_server.url))
    assert response.status_code in (400, 409, 422)


def test_update_and_delete_unknown_id_404(admin_client, stub_server, allow_private):
    assert admin_client.put("/admin/api/agents/99999", json=_agent_body(stub_server.url)).status_code == 404
    assert admin_client.delete("/admin/api/agents/99999").status_code == 404


def test_activate_one_deactivates_others(admin_client, stub_server, allow_private, cleanup_active):
    first = _create(admin_client, _agent_body(stub_server.url, name="First"))
    second = _create(admin_client, _agent_body(stub_server.url, name="Second"))

    assert admin_client.post(f"/admin/api/agents/{first}/activate").status_code == 200
    active = {a["name"]: a["is_active"] for a in _list(admin_client)}
    assert active == {"First": True, "Second": False}

    assert admin_client.post(f"/admin/api/agents/{second}/activate").status_code == 200
    active = {a["name"]: a["is_active"] for a in _list(admin_client)}
    assert active == {"First": False, "Second": True}

    assert admin_client.post("/admin/api/agents/deactivate").status_code == 200
    assert not any(a["is_active"] for a in _list(admin_client))


def test_active_agent_served_by_service(admin_client, stub_server, allow_private, cleanup_active):
    service = pytest.importorskip("admin.service", reason="admin.service not delivered yet")
    agent_id = _create(admin_client, _agent_body(stub_server.url, name="Live"))
    assert service.get_active_agent() is None
    admin_client.post(f"/admin/api/agents/{agent_id}/activate")
    active = service.get_active_agent()
    assert active is not None and active.name == "Live"
    admin_client.post("/admin/api/agents/deactivate")
    assert service.get_active_agent() is None


def test_connection_endpoint_against_stub(admin_client, stub_server, allow_private):
    agent_id = _create(admin_client, _agent_body(stub_server.url))
    response = admin_client.post(f"/admin/api/agents/{agent_id}/test", json={"message": "ping"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True, result
    assert result["reply"] == "echo: ping"
    assert isinstance(result["latency_ms"], (int, float))
    assert SECRET_HEADER_VALUE not in response.text
    headers, _ = stub_server.state.last("/echo")
    assert headers.get("authorization") == SECRET_HEADER_VALUE, "the decrypted credential is sent to the agent"


def test_connection_endpoint_reports_failure(admin_client, stub_server, allow_private):
    agent_id = _create(admin_client, _agent_body(stub_server.url, url=f"{stub_server.url}/fail"))
    response = admin_client.post(f"/admin/api/agents/{agent_id}/test", json={"message": "ping"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is False
    assert result.get("error")


def test_connection_endpoint_refuses_private_url_without_flag(admin_client, stub_server, monkeypatch):
    import config

    monkeypatch.setattr(config, "EXTERNAL_AGENT_ALLOW_PRIVATE", False)
    before = stub_server.state.hits("/echo")
    response = admin_client.post("/admin/api/agents", json=_agent_body(stub_server.url))
    if response.status_code in (400, 422):
        return  # refused at save time, which is fine too
    agent_id = id_of(response.json())
    result = admin_client.post(f"/admin/api/agents/{agent_id}/test", json={"message": "ping"}).json()
    assert result["ok"] is False
    assert stub_server.state.hits("/echo") == before
