"""External chat agents: storage, validation, testing, and the one the
voice pipeline should use right now.

Every write goes through this module, which also keeps a small in process
cache of the active agent, because get_active_agent() is called on every
voice and chat turn and must not cost a database query each time. Any
write made here invalidates the cache at once; a change made some other
way (a second server process, a manual SQL edit) is picked up within
_CACHE_TTL_SECONDS.

The auth header value is encrypted with Fernet (FIELD_ENCRYPTION_KEY, see
config.py and ADR 7) before it is stored and is never returned by any
function here that feeds the browser: agent_to_dict() only says whether
one is set.

Standalone check: python -m backend.admin.service (from the repository
root) lists the stored agents and times get_active_agent().
"""

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

import config
from agent import ExternalAgentError, ExternalHttpAgent, render_body_template
from db.base import session_scope
from db.models import AuditLog, ExternalAgent
from url_safety import UnsafeUrlError, check_outbound_url

DEFAULT_TEST_MESSAGE = "Hello, this is a connection test."
ALLOWED_METHODS = ("GET", "POST")
TIMEOUT_MIN_SECONDS = 1.0
TIMEOUT_MAX_SECONDS = 120.0
_CACHE_TTL_SECONDS = 60.0

# RFC 9110 token characters.
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}$")
# Headers the HTTP client sets itself or that would change how the request
# is framed or routed; an admin supplied value for these would either be
# ignored or break the request.
_RESERVED_HEADERS = {
    "host", "content-length", "content-type", "transfer-encoding", "connection",
    "te", "upgrade", "trailer", "keep-alive", "proxy-authorization", "proxy-connection",
    "accept-encoding",
}
_RESPONSE_PATH_PATTERN = re.compile(r"^[^.\s]+(\.[^.\s]+)*$")


class AgentValidationError(ValueError):
    """A submitted agent configuration is not acceptable; the message says
    which field and why, in words fit for the admin form."""


class AgentNotFoundError(LookupError):
    """No external agent with that id."""


# Encryption of the stored auth header value

_fernet: Optional[Fernet] = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(config.FIELD_ENCRYPTION_KEY.encode("ascii"))
    return _fernet


def encrypt_secret(plain: str) -> str:
    return _cipher().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: Optional[str]) -> Optional[str]:
    """The plain value, or None when nothing is stored or it cannot be
    decrypted (FIELD_ENCRYPTION_KEY changed since it was saved)."""
    if not token:
        return None
    try:
        return _cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# Serialization

def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def agent_to_dict(row: ExternalAgent) -> dict:
    """Everything the admin page shows. The auth header value is left out
    on purpose; has_auth_header only says whether one is stored."""
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "method": row.method,
        "auth_header_name": row.auth_header_name,
        "has_auth_header": bool(row.auth_header_value_encrypted),
        "body_template": row.body_template,
        "response_path": row.response_path,
        "timeout_seconds": row.timeout_seconds,
        "streaming": bool(row.streaming),
        "is_active": bool(row.is_active),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# Validation

def _clean_text(value: Any, field: str, max_length: int, required: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise AgentValidationError(f"{field} is required.")
    if len(text) > max_length:
        raise AgentValidationError(f"{field} is longer than {max_length} characters.")
    return text


def validate_body_template(template: str) -> str:
    template = (template or "").strip()
    if not template:
        raise AgentValidationError("Body template is required.")
    if len(template) > 10_000:
        raise AgentValidationError("Body template is longer than 10000 characters.")
    if "{text}" not in template:
        raise AgentValidationError('Body template must contain the {text} placeholder, for example {"message": "{text}"}.')
    try:
        # A sample containing quotes and a newline proves the placeholders
        # sit inside JSON strings, where escaping keeps them harmless.
        rendered = render_body_template(template, 'He said "hi"\nthen left', "Sample Name", "en")
    except ExternalAgentError as exc:
        raise AgentValidationError(str(exc)) from None
    if not isinstance(rendered, (dict, list)):
        raise AgentValidationError("Body template must be a JSON object or list.")
    return template


def _validate(data: dict, existing: Optional[ExternalAgent] = None) -> dict:
    """Checks and normalizes a submitted configuration. Returns column
    values plus two private keys: _auth_value (new plain value or None to
    keep) and _clear_auth (drop the stored value)."""
    name = _clean_text(data.get("name"), "Name", 80, required=True)
    url = _clean_text(data.get("url"), "URL", 500, required=True)
    try:
        check_outbound_url(url)
    except UnsafeUrlError as exc:
        raise AgentValidationError(f"URL: {exc}") from None

    method = _clean_text(data.get("method") or "POST", "Method", 8).upper()
    if method not in ALLOWED_METHODS:
        raise AgentValidationError("Method must be GET or POST.")

    header_name = _clean_text(data.get("auth_header_name"), "Auth header name", 80)
    header_value = data.get("auth_header_value")
    header_value = "" if header_value is None else str(header_value)
    if header_name:
        if not _HEADER_NAME_PATTERN.match(header_name):
            raise AgentValidationError("Auth header name may only contain letters, digits and - _ . characters.")
        if header_name.lower() in _RESERVED_HEADERS:
            raise AgentValidationError(f"'{header_name}' cannot be used as the auth header.")
    if header_value:
        if len(header_value) > 4000:
            raise AgentValidationError("Auth header value is longer than 4000 characters.")
        if any(ch in header_value for ch in "\r\n\0"):
            raise AgentValidationError("Auth header value cannot contain line breaks.")
        if not header_name:
            raise AgentValidationError("Enter the auth header name for this value (for example Authorization).")

    clear_auth = bool(data.get("clear_auth_header")) or not header_name
    if header_name and not header_value and not (existing and existing.auth_header_value_encrypted) and not clear_auth:
        raise AgentValidationError("Enter the auth header value, or leave the header name empty.")

    body_template = validate_body_template(data.get("body_template") or '{"message": "{text}"}')

    response_path = _clean_text(data.get("response_path"), "Response path", 200)
    if response_path and not _RESPONSE_PATH_PATTERN.match(response_path):
        raise AgentValidationError('Response path must be dot separated names or numbers, for example "choices.0.message.content".')

    timeout_raw = data.get("timeout_seconds")
    try:
        timeout = float(timeout_raw) if timeout_raw not in (None, "") else config.EXTERNAL_AGENT_DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        raise AgentValidationError("Timeout must be a number of seconds.") from None
    if not TIMEOUT_MIN_SECONDS <= timeout <= TIMEOUT_MAX_SECONDS:
        raise AgentValidationError(
            f"Timeout must be between {TIMEOUT_MIN_SECONDS:g} and {TIMEOUT_MAX_SECONDS:g} seconds."
        )

    return {
        "name": name,
        "url": url,
        "method": method,
        "auth_header_name": header_name or None,
        "body_template": body_template,
        "response_path": response_path,
        "timeout_seconds": timeout,
        "streaming": bool(data.get("streaming")),
        "_auth_value": header_value or None,
        "_clear_auth": clear_auth and not header_value,
    }


def _apply(row: ExternalAgent, cleaned: dict) -> None:
    for column in ("name", "url", "method", "auth_header_name", "body_template",
                   "response_path", "timeout_seconds", "streaming"):
        setattr(row, column, cleaned[column])
    if cleaned["_auth_value"]:
        row.auth_header_value_encrypted = encrypt_secret(cleaned["_auth_value"])
    elif cleaned["_clear_auth"]:
        row.auth_header_value_encrypted = None
    row.updated_at = datetime.now(timezone.utc)


def _name_taken(session, name: str, exclude_id: Optional[int] = None) -> bool:
    query = select(ExternalAgent.id).where(ExternalAgent.name == name)
    if exclude_id is not None:
        query = query.where(ExternalAgent.id != exclude_id)
    return session.execute(query).first() is not None


# CRUD

def list_agents() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(ExternalAgent).order_by(ExternalAgent.name)).all()
        return [agent_to_dict(row) for row in rows]


def get_agent(agent_id: int) -> dict:
    with session_scope() as session:
        row = session.get(ExternalAgent, agent_id)
        if row is None:
            raise AgentNotFoundError(f"No external agent with id {agent_id}.")
        return agent_to_dict(row)


def create_agent(data: dict) -> dict:
    cleaned = _validate(data)
    try:
        with session_scope() as session:
            if _name_taken(session, cleaned["name"]):
                raise AgentValidationError(f"An agent named '{cleaned['name']}' already exists.")
            row = ExternalAgent(is_active=False, created_at=datetime.now(timezone.utc))
            _apply(row, cleaned)
            session.add(row)
            session.flush()
            result = agent_to_dict(row)
    except IntegrityError:
        raise AgentValidationError(f"An agent named '{cleaned['name']}' already exists.") from None
    invalidate_cache()
    return result


def update_agent(agent_id: int, data: dict) -> dict:
    """Full update of the editable fields. A blank auth header value keeps
    the stored one; clear_auth_header=True, or an empty header name,
    removes it."""
    try:
        with session_scope() as session:
            row = session.get(ExternalAgent, agent_id)
            if row is None:
                raise AgentNotFoundError(f"No external agent with id {agent_id}.")
            cleaned = _validate(data, existing=row)
            if _name_taken(session, cleaned["name"], exclude_id=agent_id):
                raise AgentValidationError(f"An agent named '{cleaned['name']}' already exists.")
            _apply(row, cleaned)
            session.flush()
            result = agent_to_dict(row)
    except IntegrityError:
        raise AgentValidationError("An agent with that name already exists.") from None
    invalidate_cache()
    return result


def delete_agent(agent_id: int) -> None:
    """Deleting the active agent hands the voice pipeline back to the
    built in assistant."""
    with session_scope() as session:
        row = session.get(ExternalAgent, agent_id)
        if row is None:
            raise AgentNotFoundError(f"No external agent with id {agent_id}.")
        session.delete(row)
    invalidate_cache()


def activate(agent_id: int) -> dict:
    """Makes this agent the one that answers, and every other one inactive,
    in one transaction, so there is never more than one active agent."""
    with session_scope() as session:
        row = session.get(ExternalAgent, agent_id)
        if row is None:
            raise AgentNotFoundError(f"No external agent with id {agent_id}.")
        session.execute(
            update(ExternalAgent)
            .where(ExternalAgent.id != agent_id, ExternalAgent.is_active.is_(True))
            .values(is_active=False)
        )
        row.is_active = True
        session.flush()
        result = agent_to_dict(row)
    invalidate_cache()
    return result


def deactivate_all() -> None:
    """No external agent: the built in assistant answers."""
    with session_scope() as session:
        session.execute(update(ExternalAgent).where(ExternalAgent.is_active.is_(True)).values(is_active=False))
    invalidate_cache()


# Building and testing agents

def _build_agent(row_values: dict, auth_value: Optional[str]) -> ExternalHttpAgent:
    return ExternalHttpAgent(
        name=row_values["name"],
        url=row_values["url"],
        method=row_values["method"],
        auth_header_name=row_values.get("auth_header_name"),
        auth_header_value=auth_value,
        body_template=row_values["body_template"],
        response_path=row_values.get("response_path") or "",
        timeout_seconds=row_values.get("timeout_seconds"),
        streaming=bool(row_values.get("streaming")),
    )


def _agent_from_row(row: ExternalAgent) -> ExternalHttpAgent:
    auth_value = decrypt_secret(row.auth_header_value_encrypted)
    if row.auth_header_value_encrypted and auth_value is None:
        raise ExternalAgentError(
            "The stored auth header value cannot be decrypted (FIELD_ENCRYPTION_KEY changed); enter it again."
        )
    return _build_agent(
        {
            "name": row.name, "url": row.url, "method": row.method,
            "auth_header_name": row.auth_header_name, "body_template": row.body_template,
            "response_path": row.response_path, "timeout_seconds": row.timeout_seconds,
            "streaming": row.streaming,
        },
        auth_value,
    )


def test_agent(
    agent_id: Optional[int] = None,
    draft: Optional[dict] = None,
    message: str = DEFAULT_TEST_MESSAGE,
) -> dict:
    """Sends one message and reports {ok, reply, latency_ms, error}.

    agent_id alone tests a saved agent. draft alone tests an unsaved
    configuration from the form. Both together test the form's edits to a
    saved agent, using the stored auth header value when the form left it
    blank. A bad configuration or a failed call comes back as ok False
    with the reason; only an unknown agent_id raises (AgentNotFoundError).
    """
    message = (message or DEFAULT_TEST_MESSAGE).strip()[:2000] or DEFAULT_TEST_MESSAGE
    try:
        if draft is None:
            if agent_id is None:
                raise AgentValidationError("Nothing to test.")
            with session_scope() as session:
                row = session.get(ExternalAgent, agent_id)
                if row is None:
                    raise AgentNotFoundError(f"No external agent with id {agent_id}.")
                agent = _agent_from_row(row)
        else:
            stored_auth = None
            existing = None
            if agent_id is not None:
                with session_scope() as session:
                    existing = session.get(ExternalAgent, agent_id)
                    if existing is None:
                        raise AgentNotFoundError(f"No external agent with id {agent_id}.")
                    stored_auth = decrypt_secret(existing.auth_header_value_encrypted)
            cleaned = _validate(draft, existing=existing)
            auth_value = cleaned["_auth_value"] or (None if cleaned["_clear_auth"] else stored_auth)
            agent = _build_agent(cleaned, auth_value)
    except (AgentValidationError, ExternalAgentError) as exc:
        return {"ok": False, "reply": None, "latency_ms": None, "error": str(exc)}

    start = time.perf_counter()
    try:
        reply = agent.respond(message, user_name=None, language="en")
        return {"ok": True, "reply": reply, "latency_ms": int((time.perf_counter() - start) * 1000), "error": None}
    except ExternalAgentError as exc:
        error = str(exc)
    except Exception as exc:  # a bug must still come back as a readable test result
        error = f"Unexpected error ({type(exc).__name__})."
    return {"ok": False, "reply": None, "latency_ms": int((time.perf_counter() - start) * 1000), "error": error}


# The active agent, cached

# None: not loaded. Otherwise (loaded_at, agent_id or None, name or None,
# agent or None), replaced as a whole so a reader never sees half of it.
_cache_entry: Optional[tuple] = None
_cache_lock = threading.Lock()
_cache_generation = 0


def invalidate_cache() -> None:
    global _cache_entry, _cache_generation
    with _cache_lock:
        _cache_entry = None
        _cache_generation += 1


def _load_active() -> tuple:
    global _cache_entry
    entry = _cache_entry
    if entry is not None and time.monotonic() - entry[0] < _CACHE_TTL_SECONDS:
        return entry
    with _cache_lock:
        generation = _cache_generation
    try:
        with session_scope() as session:
            row = session.scalars(select(ExternalAgent).where(ExternalAgent.is_active.is_(True)).limit(1)).first()
            if row is None:
                loaded = (time.monotonic(), None, None, None)
            else:
                try:
                    agent = _agent_from_row(row)
                except ExternalAgentError as exc:
                    print(f"External agent '{row.name}' is active but unusable ({exc}); using the built in assistant.")
                    agent = None
                loaded = (time.monotonic(), row.id, row.name, agent)
    except Exception as exc:
        # No database yet, or it is unreachable: answer with the built in
        # assistant and try again on the next turn rather than failing it.
        print(f"Could not read the active external agent ({type(exc).__name__}: {exc}).")
        return (time.monotonic(), None, None, None)
    with _cache_lock:
        if generation == _cache_generation:
            _cache_entry = loaded
    return loaded


def get_active_agent() -> Optional[ExternalHttpAgent]:
    """The external agent that should answer now, or None for the built in
    assistant. Called on every turn: served from memory, no database
    query, unless a write happened or the cache is older than a minute.
    Never raises."""
    return _load_active()[3]


def active_agent_summary() -> Optional[dict]:
    """{id, name} of the active external agent for /health, or None."""
    _, agent_id, name, agent = _load_active()
    if agent_id is None:
        return None
    return {"id": agent_id, "name": name, "usable": agent is not None}


# Admin action audit

def record_admin_action(admin_id: str, action: str, outcome: str = "ok", detail: Optional[str] = None) -> None:
    """One audit row per admin change (never any secret). Never raises."""
    try:
        with session_scope() as session:
            session.add(
                AuditLog(
                    created_at=datetime.now(timezone.utc),
                    actor_type="admin",
                    actor_id=(admin_id or "admin")[:64],
                    action=action[:80],
                    outcome=outcome[:20],
                    detail=detail[:300] if detail else None,
                )
            )
    except Exception as exc:
        print(f"Admin audit write failed ({type(exc).__name__}: {exc})")


if __name__ == "__main__":
    agents = list_agents()
    print(json.dumps(agents, indent=2))
    for attempt in range(3):
        start = time.perf_counter()
        active = get_active_agent()
        print(f"get_active_agent(): {active!r} in {(time.perf_counter() - start) * 1000:.2f} ms")
