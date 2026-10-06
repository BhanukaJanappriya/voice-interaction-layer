"""The hub's search tool stays out of spoken replies unless asked for.

Measured against the real hub, a knowledge search takes 3 to 7 seconds, far
beyond the first word budget, and a timeout there would also open the breaker
the hub's one time code tools share. MCP_HUB_IN_REPLIES is therefore off by
default; the hub is still used for one time codes either way.
"""

import importlib

import config

tools = importlib.import_module("mcp_client.tools")


def test_default_is_off_in_config_source():
    # The suite pins the flag on for the mock hub tests, so read the source
    # default instead of the running value.
    text = (config.PROJECT_ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    assert 'MCP_HUB_IN_REPLIES = _env_bool("MCP_HUB_IN_REPLIES", False)' in text


def test_no_hub_call_is_made_when_the_flag_is_off(monkeypatch):
    monkeypatch.setattr(config, "MCP_HUB_IN_REPLIES", False)

    def must_not_be_called():
        raise AssertionError("the hub was contacted although MCP_HUB_IN_REPLIES is off")

    monkeypatch.setattr(tools, "get_hub", must_not_be_called)
    assert tools.hub_context_for("how much is roaming?") is None
