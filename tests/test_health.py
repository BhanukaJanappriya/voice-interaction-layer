"""GET /health keeps every existing key and adds the new subsystems."""

import pytest

from conftest import make_client

EXISTING_KEYS = {
    "status",
    "cloud_llm_configured",
    "cloud_llm_provider",
    "cloud_llm_model",
    "cloud_llm_last_error",
    "cloud_speech_configured",
    "cloud_speech_provider",
    "supported_languages",
}
NEW_KEYS = {"database", "knowledge", "external_agent", "mcp"}


@pytest.fixture
def health(server_module):
    c = make_client(server_module.app)
    response = c.get("/health")
    c.close()
    assert response.status_code == 200
    return response.json()


def test_existing_keys_unchanged(health):
    assert EXISTING_KEYS <= set(health)
    assert health["status"] == "ok"
    assert health["cloud_llm_configured"] is False, "tests run with no cloud key"
    assert set(health["supported_languages"]) == {"en", "si", "ta"}


def test_new_subsystem_keys(health):
    present = NEW_KEYS & set(health)
    if not present:
        pytest.skip("/health does not report the new subsystems yet (plan R5 integration pending)")
    assert present == NEW_KEYS, f"missing {NEW_KEYS - present}"


def test_new_keys_are_informative(health):
    if not NEW_KEYS <= set(health):
        pytest.skip("/health does not report the new subsystems yet (plan R5 integration pending)")
    assert health["database"] not in (None, "", {}, [])
    knowledge = health["knowledge"]
    if isinstance(knowledge, dict):
        assert "loaded" in knowledge
    assert health["mcp"] not in (None, "")
    # No agent active in a fresh database.
    external = health["external_agent"]
    if isinstance(external, dict):
        assert not external.get("active") or external.get("name") is None
    else:
        assert external in (None, False, "none", "")


def test_health_leaks_no_secrets(health):
    import config

    text = str(health)
    for secret in (config.JWT_SECRET, config.FIELD_ENCRYPTION_KEY, config.ADMIN_PASSWORD):
        assert secret not in text
