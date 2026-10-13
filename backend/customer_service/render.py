"""Turn hub tool output into labelled text blocks for the local model.

Response shapes of the customer tools are unverified (docs/MCP_HUB.md), so
parsing is defensive: plain text, structured content, JSON text, and JSON
wrapped in "data" or "result" all work. Keys that look like secrets are dropped
at any depth, and the whole context is cut to a character limit while every
tool keeps a block.
"""

import json
import re
from typing import Any, Optional

HEADER = "SLT ACCOUNT DATA of the verified customer (read only, from the SLT systems):"
TRIM_MARK = " ...(trimmed)"
EMPTY = "(no data returned)"

_SECRET_KEYS = {"token", "accesstoken", "refreshtoken", "idtoken", "password", "secret", "otp", "authorization"}
_SECRET_SUFFIXES = ("token", "password", "secret")
_WRAPPER_KEYS = ("data", "result")
_NOISE_KEYS = {"status", "success", "message", "code", "error", "ok"}
_TEXT_SECRET = re.compile(
    r"(?i)\b(access[_ ]?token|refresh[_ ]?token|token|password|secret|otp|authorization)\b(\s*[:=]\s*)(\S+)"
)
_AUTH_TEXT = re.compile(
    r"(?i)authenticat|not logged in|session expired|unauthor|\b401\b|please (log ?in|verify)"
)


def _is_secret_key(key: Any) -> bool:
    flat = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return flat in _SECRET_KEYS or flat.endswith(_SECRET_SUFFIXES)


def clean(value: Any) -> Any:
    """A copy without secret looking keys, at any depth."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if not _is_secret_key(k)}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def _parse_json(text: str) -> Optional[Any]:
    text = (text or "").strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _unwrap(value: Any, depth: int = 0) -> Any:
    """Peel {"data": X}, {"result": X} (status style siblings allowed) and JSON
    that was sent as a string inside the wrapper."""
    if depth > 3:
        return value
    if isinstance(value, dict):
        for key in _WRAPPER_KEYS:
            if key in value and set(value) - {key} <= _NOISE_KEYS and value[key] not in (None, ""):
                return _unwrap(value[key], depth + 1)
    if isinstance(value, str):
        parsed = _parse_json(value)
        if parsed is not None:
            return _unwrap(parsed, depth + 1)
    return value


def extract(output: Any) -> Any:
    """The payload of a client.ToolOutput (or a plain string / dict): a
    dict, list or string, not yet cleaned."""
    if isinstance(output, (dict, list)):
        return _unwrap(output)
    if isinstance(output, str):
        text, structured = output, None
    else:
        text = getattr(output, "text", "") or ""
        structured = getattr(output, "structured", None)
    parsed = _parse_json(text)
    if parsed is not None:
        return _unwrap(parsed)
    if text.strip():
        return text.strip()
    if structured not in (None, "", {}, []):
        return _unwrap(structured)
    return ""


def output_text(output: Any) -> str:
    """Lowercase free text of an output, for product type detection only."""
    payload = extract(output)
    return (payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)).lower()


def payload_error(payload: Any) -> Optional[str]:
    """The error message when a successful hub call carries an error inside
    its body ({"status": "error", ...}, {"success": false}, or a short plain
    text saying the customer is not authenticated); None otherwise."""
    if isinstance(payload, dict):
        status = str(payload.get("status", "")).lower()
        failed = status in ("error", "failed", "failure", "fail") or payload.get("success") is False
        if failed:
            return str(payload.get("message") or payload.get("error") or "tool reported an error")[:300]
        return None
    if isinstance(payload, str) and len(payload) < 200 and _AUTH_TEXT.search(payload):
        return payload
    return None


def body(payload: Any) -> str:
    """Compact text of one payload with secrets removed."""
    payload = clean(payload)
    if isinstance(payload, str):
        text = _TEXT_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[removed]", payload)
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = text.strip()
    return text or EMPTY


def block(name: str, output: Any) -> tuple:
    """(tool name, body text) for one answered tool."""
    return (name, body(extract(output)))


def _shares(lengths: list, room: int) -> list:
    """Fair division of room: short bodies keep their length, the rest split
    what is left evenly."""
    shares = [0] * len(lengths)
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    left = max(room, 0)
    for position, index in enumerate(order):
        fair = left // (len(order) - position)
        shares[index] = min(lengths[index], fair)
        left -= shares[index]
    return shares


def assemble(blocks: list, max_chars: int) -> str:
    """The context text: a header, then "[tool]" and the body for every tool,
    cut to max_chars with every tool still represented."""
    if not blocks:
        return ""
    max_chars = max(int(max_chars), 0)
    labels = [f"[{name}]\n" for name, _ in blocks]
    overhead = len(HEADER) + 1 + sum(len(label) + 1 for label in labels)
    bodies = [text for _, text in blocks]
    shares = _shares([len(b) for b in bodies], max_chars - overhead)
    parts = [HEADER]
    for label, text, share in zip(labels, bodies, shares):
        if share < len(text):
            if share > len(TRIM_MARK):
                text = text[: share - len(TRIM_MARK)].rstrip() + TRIM_MARK
            else:
                text = text[:share]
        parts.append(label + text)
    result = "\n".join(parts)
    return result[:max_chars] if max_chars and len(result) > max_chars else result
