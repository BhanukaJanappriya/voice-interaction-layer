"""Tool declarations for a future cloud function calling path.

schemas_for(access) returns the tools the gateway allows this caller, in the
OpenAI/Gemini function declaration shape:

    [{"name": ..., "description": ..., "parameters": {"type": "object", ...}}]

telephone_no is never in a schema (the gateway forces it), sign in tools and
tools that change an account are never listed, and a guest gets only the
knowledge search. Nothing here is used by the current answering path, which
retrieves deterministically (see tool_agent.core). Customer tools must only be
offered to a model that may see customer data.
"""

from typing import Any

import mcp_gateway

_DROP_KEYS = {"title", "additionalProperties", "$schema", "default"}


def _clean(value: Any, names: bool = False) -> Any:
    """names=True: value is a "properties" mapping, whose keys are argument
    names and must never be dropped."""
    if isinstance(value, dict):
        return {
            k: _clean(v, k == "properties" and not names)
            for k, v in value.items()
            if names or k not in _DROP_KEYS
        }
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def schemas_for(access) -> list:
    try:
        listed = mcp_gateway.tool_schemas(access)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for tool in listed:
        parameters = _clean(tool.get("input_schema") or {"type": "object", "properties": {}})
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        parameters["properties"].pop("telephone_no", None)
        out.append({"name": tool["name"], "description": tool.get("description") or "", "parameters": parameters})
    return out
