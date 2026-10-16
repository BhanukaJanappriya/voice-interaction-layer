"""The one time code backend default."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

def test_the_one_time_code_goes_through_the_hub_by_default_when_the_hub_is_the_only_source():
    """VERIFY_OTP_BACKEND left empty: hub when ANSWER_SOURCE is mcp_hub and a hub key is set, else local."""
    import subprocess

    def backend(env):
        out = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, 'backend'); import config; print(config.VERIFY_OTP_BACKEND)"],
            cwd=os.path.join(os.path.dirname(__file__), ".."), capture_output=True, text=True, timeout=60,
            env={**os.environ, "VERIFY_OTP_BACKEND": "", **env},
        )
        return out.stdout.strip().splitlines()[-1]

    assert backend({"ANSWER_SOURCE": "mcp_hub", "MCP_HUB_API_KEY": "k"}) == "hub"
    assert backend({"ANSWER_SOURCE": "mcp_hub", "MCP_HUB_API_KEY": ""}) == "local"
    assert backend({"ANSWER_SOURCE": "local_rag", "MCP_HUB_API_KEY": "k"}) == "local"
    assert backend({"ANSWER_SOURCE": "mcp_hub", "MCP_HUB_API_KEY": "k", "VERIFY_OTP_BACKEND": "local"}) == "local"
