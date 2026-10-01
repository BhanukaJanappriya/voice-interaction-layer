"""MCP client registry and audit trail.

A machine client (another agent, an IDE, a test script) gets a client id
and a secret from the admin portal, exchanges them at /oauth/token for a
short lived access token, and calls the /mcp tools with it. This module is
the only place those clients and their audit rows are read or written, so
the admin portal (Agent B), the token endpoint (oauth.py) and the MCP
server (server.py) all agree on what "active" and "revoked" mean.

The secret is shown once, at creation, and only its bcrypt hash is kept
(auth.passwords), so a copied database does not hand out working
credentials. Revocation is a timestamp rather than a delete, so the audit
rows that point at a revoked client still say which client it was.

Every function opens its own short session_scope() and returns plain
values or detached rows, never a live session, because callers run on
worker threads (bcrypt is slow on purpose) and the MCP tools run inside
the SDK's task group, where sharing one session would not be safe.
"""

import secrets
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import func, select

from auth.passwords import hash_secret, verify_secret
from db.base import session_scope
from db.models import AuditLog, McpClient

# Every scope a client can be granted. Kept here, next to the tools that
# enforce them (server.py maps each tool to one of these), so the admin
# portal, the token endpoint and the metadata documents list the same set.
ALLOWED_SCOPES = {"knowledge:read", "assistant:ask"}

ACTOR_TYPE_MCP = "mcp_client"

_NAME_MAX = 80
_DETAIL_MAX = 300
_ACTOR_ID_MAX = 64
_ACTION_MAX = 80


class InvalidClientRequest(ValueError):
    """The name or the scopes given for a new client are not acceptable.
    A ValueError subclass so a caller that only catches ValueError (the
    admin API turning it into a 400) still handles it."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    # SQLite hands back naive datetimes even for timezone aware columns;
    # everything this app stores is UTC, so say so explicitly.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def normalize_scopes(scopes: Iterable[str]) -> list[str]:
    """Deduplicated, sorted, validated scope list. Raises
    InvalidClientRequest for an empty list or any unknown scope, naming it,
    so a typo in the admin form does not silently create a useless client."""
    cleaned = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    if not cleaned:
        raise InvalidClientRequest("at least one scope is required")
    unknown = [scope for scope in cleaned if scope not in ALLOWED_SCOPES]
    if unknown:
        raise InvalidClientRequest(
            f"unknown scope(s): {', '.join(unknown)}; allowed: {', '.join(sorted(ALLOWED_SCOPES))}"
        )
    return cleaned


def client_scopes(client: McpClient) -> list[str]:
    return sorted(set((client.scopes or "").split()))


def create_client(name: str, scopes: list[str]) -> tuple[McpClient, str]:
    """Creates a client and returns (row, plain_secret). The plain secret
    exists only in this return value; show it once and discard it."""
    name = (name or "").strip()
    if not name:
        raise InvalidClientRequest("name is required")
    if len(name) > _NAME_MAX:
        raise InvalidClientRequest(f"name is longer than {_NAME_MAX} characters")
    cleaned = normalize_scopes(scopes)

    # 16 random bytes behind a readable prefix: unguessable, and the
    # prefix makes a leaked id recognisable in logs and secret scanners.
    client_id = "mcp_" + secrets.token_urlsafe(16)
    plain_secret = secrets.token_urlsafe(32)
    client = McpClient(
        client_id=client_id,
        name=name,
        secret_hash=hash_secret(plain_secret),
        scopes=" ".join(cleaned),
        created_at=_now(),
    )
    with session_scope() as session:
        session.add(client)
        session.flush()
        session.refresh(client)
        session.expunge(client)
    return client, plain_secret


def _client_dict(client: McpClient) -> dict:
    return {
        "id": client.id,
        "client_id": client.client_id,
        "name": client.name,
        "scopes": client_scopes(client),
        "created_at": _iso(client.created_at),
        "last_used_at": _iso(client.last_used_at),
        "revoked_at": _iso(client.revoked_at),
        "active": client.revoked_at is None,
    }


def list_clients() -> list[dict]:
    """Every client, newest first, without secret hashes."""
    with session_scope() as session:
        rows = session.scalars(select(McpClient).order_by(McpClient.id.desc())).all()
        return [_client_dict(row) for row in rows]


def revoke_client(id: int) -> bool:
    """Revokes by database id. True if the client exists (revoking twice is
    still True, and keeps the first revocation time). Tokens already issued
    stop working on their next request, because the MCP token verifier
    checks is_client_active() every time."""
    with session_scope() as session:
        client = session.get(McpClient, id)
        if client is None:
            return False
        if client.revoked_at is None:
            client.revoked_at = _now()
        return True


def authenticate_client(client_id: str, secret: str) -> Optional[McpClient]:
    """The client if the id exists, is not revoked and the secret matches;
    otherwise None. A bcrypt comparison runs in every case (against a dummy
    hash for an unknown id), so response time does not reveal which ids
    exist. Marks last_used_at on success."""
    client_id = (client_id or "")[:_ACTOR_ID_MAX]
    secret = secret or ""
    with session_scope() as session:
        client = session.scalars(select(McpClient).where(McpClient.client_id == client_id)).first()
        matches = verify_secret(secret, client.secret_hash if client is not None else None)
        if client is None or not matches or client.revoked_at is not None:
            return None
        client.last_used_at = _now()
        session.flush()
        session.refresh(client)
        session.expunge(client)
        return client


def active_client_pk(client_id: str) -> Optional[int]:
    """Database id of the client if it exists and is not revoked, else
    None. One indexed lookup, cheap enough to run on every /mcp request."""
    if not client_id:
        return None
    with session_scope() as session:
        row = session.execute(
            select(McpClient.id, McpClient.revoked_at).where(McpClient.client_id == client_id)
        ).first()
        if row is None or row.revoked_at is not None:
            return None
        return row.id


def is_client_active(client_id: str) -> bool:
    return active_client_pk(client_id) is not None


def count_active_clients() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count(McpClient.id)).where(McpClient.revoked_at.is_(None))) or 0


def record_audit(
    actor_id: str,
    action: str,
    outcome: str,
    detail: Optional[str] = None,
    duration_ms: Optional[int] = None,
    mcp_client_pk: Optional[int] = None,
    actor_type: str = ACTOR_TYPE_MCP,
) -> None:
    """Writes one audit row. Callers pass only metadata (tool name,
    outcome, a short reason such as "insufficient_scope"), never the
    question or the answer. Never raises: an audit write failing must not
    turn a successful tool call into an error, so a failure is printed."""
    try:
        with session_scope() as session:
            session.add(
                AuditLog(
                    created_at=_now(),
                    actor_type=(actor_type or ACTOR_TYPE_MCP)[:20],
                    actor_id=(actor_id or "unknown")[:_ACTOR_ID_MAX],
                    action=(action or "unknown")[:_ACTION_MAX],
                    outcome=(outcome or "unknown")[:20],
                    detail=detail[:_DETAIL_MAX] if detail else None,
                    duration_ms=int(duration_ms) if duration_ms is not None else None,
                    mcp_client_id=mcp_client_pk,
                )
            )
    except Exception as exc:
        print(f"MCP audit write failed ({type(exc).__name__}: {exc})")


def list_audit(limit: int = 100) -> list[dict]:
    """Most recent audit rows first, with the client's readable name when
    the row belongs to an MCP client that still exists."""
    limit = max(1, min(int(limit or 100), 1000))
    with session_scope() as session:
        rows = session.execute(
            select(AuditLog, McpClient.name)
            .outerjoin(McpClient, AuditLog.mcp_client_id == McpClient.id)
            .order_by(AuditLog.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": entry.id,
                "created_at": _iso(entry.created_at),
                "actor_type": entry.actor_type,
                "actor_id": entry.actor_id,
                "client_name": client_name,
                "action": entry.action,
                "outcome": entry.outcome,
                "detail": entry.detail,
                "duration_ms": entry.duration_ms,
                "mcp_client_id": entry.mcp_client_id,
            }
            for entry, client_name in rows
        ]


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
