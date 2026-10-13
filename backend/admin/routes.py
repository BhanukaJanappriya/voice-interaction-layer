"""Admin portal HTTP API, mounted at /admin/api.

Every endpoint requires an admin session (auth.dependencies.require_admin,
which reads the admin_session cookie and answers 401 otherwise). Handlers
are plain def functions, so FastAPI runs their blocking database and
outbound HTTP work on its thread pool instead of on the event loop.

MCP client management calls mcp_server.clients, imported inside each
handler so this module imports (and the agent endpoints work) even where
the MCP package is missing or disabled.
"""

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from admin import service

try:
    from auth.dependencies import require_admin
except ImportError:  # auth package not installed yet: fail closed
    def require_admin() -> dict:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin sign in is not available.")


router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[Depends(require_admin)])


# Models

class AgentIn(BaseModel):
    name: str = Field(..., max_length=80)
    url: str = Field(..., max_length=500)
    method: Literal["GET", "POST"] = "POST"
    auth_header_name: Optional[str] = Field(None, max_length=80)
    # Write only: blank keeps the stored value on update.
    auth_header_value: Optional[str] = Field(None, max_length=4000)
    clear_auth_header: bool = False
    body_template: str = Field('{"message": "{text}"}', max_length=10_000)
    response_path: str = Field("reply", max_length=200)
    timeout_seconds: Optional[float] = None
    streaming: bool = False


class AgentOut(BaseModel):
    id: int
    name: str
    url: str
    method: str
    auth_header_name: Optional[str]
    has_auth_header: bool
    body_template: str
    response_path: str
    timeout_seconds: float
    streaming: bool
    is_active: bool
    created_at: Optional[str]
    updated_at: Optional[str]


class AgentList(BaseModel):
    agents: list[AgentOut]
    # None means the built in assistant answers.
    active_agent_id: Optional[int]


class TestMessage(BaseModel):
    message: Optional[str] = Field(None, max_length=2000)


class DraftTest(AgentIn):
    # Set when testing unsaved edits to a saved agent, so a blank auth
    # header value falls back to the stored one.
    id: Optional[int] = None
    message: Optional[str] = Field(None, max_length=2000)


class TestResult(BaseModel):
    ok: bool
    reply: Optional[str]
    latency_ms: Optional[int]
    error: Optional[str]


class McpClientIn(BaseModel):
    name: str = Field(..., max_length=80)
    scopes: list[str] = Field(..., min_length=1, max_length=10)


class McpClientOut(BaseModel):
    id: int
    client_id: str
    name: str
    scopes: list[str]
    created_at: Optional[str] = None
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None
    active: bool


class McpClientCreated(BaseModel):
    client: McpClientOut
    # Shown once. Only its hash is stored.
    client_secret: str


class AuditEntry(BaseModel):
    id: int
    created_at: Optional[str]
    actor_type: str
    actor_id: str
    client_name: Optional[str] = None
    action: str
    outcome: str
    detail: Optional[str] = None
    duration_ms: Optional[int] = None


class OkResult(BaseModel):
    ok: bool = True


# Helpers

def _admin_id(claims: Any) -> str:
    if isinstance(claims, dict):
        return str(claims.get("username") or claims.get("sub") or "admin")
    return str(getattr(claims, "username", None) or getattr(claims, "sub", None) or "admin")


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _mcp_clients():
    try:
        from mcp_server import clients
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP client management is not available on this server.",
        ) from None
    return clients


def _field(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _iso(value) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    return service._iso(value)


def _client_out(obj: Any) -> McpClientOut:
    scopes = _field(obj, "scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    revoked_at = _iso(_field(obj, "revoked_at"))
    active = _field(obj, "active")
    return McpClientOut(
        id=_field(obj, "id"),
        client_id=_field(obj, "client_id"),
        name=_field(obj, "name"),
        scopes=sorted(scopes),
        created_at=_iso(_field(obj, "created_at")),
        last_used_at=_iso(_field(obj, "last_used_at")),
        revoked_at=revoked_at,
        active=bool(active) if active is not None else revoked_at is None,
    )


# External agents

@router.get("/agents", response_model=AgentList)
def list_agents():
    agents = service.list_agents()
    active = next((agent["id"] for agent in agents if agent["is_active"]), None)
    return {"agents": agents, "active_agent_id": active}


@router.post("/agents", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(body: AgentIn, claims=Depends(require_admin)):
    try:
        agent = service.create_agent(body.model_dump())
    except service.AgentValidationError as exc:
        raise _bad_request(exc) from None
    service.record_admin_action(_admin_id(claims), "agent.create", detail=f"agent {agent['id']} {agent['name']}")
    return agent


@router.put("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, body: AgentIn, claims=Depends(require_admin)):
    try:
        agent = service.update_agent(agent_id, body.model_dump())
    except service.AgentNotFoundError as exc:
        raise _not_found(exc) from None
    except service.AgentValidationError as exc:
        raise _bad_request(exc) from None
    service.record_admin_action(_admin_id(claims), "agent.update", detail=f"agent {agent_id} {agent['name']}")
    return agent


@router.delete("/agents/{agent_id}", response_model=OkResult)
def delete_agent(agent_id: int, claims=Depends(require_admin)):
    try:
        service.delete_agent(agent_id)
    except service.AgentNotFoundError as exc:
        raise _not_found(exc) from None
    service.record_admin_action(_admin_id(claims), "agent.delete", detail=f"agent {agent_id}")
    return {"ok": True}


# Declared before /agents/{agent_id}/... routes that share a prefix, and
# with a literal path, so "test" and "deactivate" are never read as ids.
@router.post("/agents/test", response_model=TestResult)
def test_unsaved_agent(body: DraftTest):
    draft = body.model_dump(exclude={"id", "message"})
    try:
        return service.test_agent(agent_id=body.id, draft=draft, message=body.message or service.DEFAULT_TEST_MESSAGE)
    except service.AgentNotFoundError as exc:
        raise _not_found(exc) from None


@router.post("/agents/deactivate", response_model=OkResult)
def deactivate_agents(claims=Depends(require_admin)):
    service.deactivate_all()
    service.record_admin_action(_admin_id(claims), "agent.deactivate_all")
    return {"ok": True}


@router.post("/agents/{agent_id}/test", response_model=TestResult)
def test_saved_agent(agent_id: int, body: Optional[TestMessage] = None):
    message = (body.message if body else None) or service.DEFAULT_TEST_MESSAGE
    try:
        return service.test_agent(agent_id=agent_id, message=message)
    except service.AgentNotFoundError as exc:
        raise _not_found(exc) from None


@router.post("/agents/{agent_id}/activate", response_model=AgentOut)
def activate_agent(agent_id: int, claims=Depends(require_admin)):
    try:
        agent = service.activate(agent_id)
    except service.AgentNotFoundError as exc:
        raise _not_found(exc) from None
    service.record_admin_action(_admin_id(claims), "agent.activate", detail=f"agent {agent_id} {agent['name']}")
    return agent


# MCP clients and audit

@router.get("/mcp-clients", response_model=list[McpClientOut])
def list_mcp_clients():
    return [_client_out(client) for client in _mcp_clients().list_clients()]


@router.post("/mcp-clients", response_model=McpClientCreated, status_code=status.HTTP_201_CREATED)
def create_mcp_client(body: McpClientIn, response: Response, claims=Depends(require_admin)):
    clients = _mcp_clients()
    try:
        client, plain_secret = clients.create_client(body.name.strip(), body.scopes)
    except ValueError as exc:
        raise _bad_request(exc) from None
    # The one response that carries the secret must not be kept by any cache.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    out = _client_out(client)
    service.record_admin_action(_admin_id(claims), "mcp_client.create", detail=f"client {out.client_id}")
    return {"client": out, "client_secret": plain_secret}


@router.delete("/mcp-clients/{client_pk}", response_model=OkResult)
def revoke_mcp_client(client_pk: int, claims=Depends(require_admin)):
    if not _mcp_clients().revoke_client(client_pk):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No MCP client with id {client_pk}.")
    service.record_admin_action(_admin_id(claims), "mcp_client.revoke", detail=f"client {client_pk}")
    return {"ok": True}


@router.get("/audit", response_model=list[AuditEntry])
def list_audit(limit: int = Query(100, ge=1, le=500)):
    entries = _mcp_clients().list_audit(limit=limit)
    return [
        AuditEntry(
            id=_field(entry, "id"),
            created_at=_iso(_field(entry, "created_at")),
            actor_type=_field(entry, "actor_type") or "",
            actor_id=_field(entry, "actor_id") or "",
            client_name=_field(entry, "client_name"),
            action=_field(entry, "action") or "",
            outcome=_field(entry, "outcome") or "",
            detail=_field(entry, "detail"),
            duration_ms=_field(entry, "duration_ms"),
        )
        for entry in entries
    ]


@router.get("/hub")
def hub_gateway():
    """For the admin page: the MCP gateway's state (open customer connections,
    last error), the hub's own latency classes per tool (get_function_latencies,
    a static classification meant for operators, not customers), the tool agent's
    per turn tool time, and the last gateway calls (tool, outcome, milliseconds;
    never arguments, results or numbers)."""
    import mcp_gateway
    import tool_agent
    from mcp_gateway import audit

    return {
        "gateway": mcp_gateway.status(),
        "tool_latency_classes": mcp_gateway.latencies() or None,
        "tool_agent": tool_agent.stats(),
        "recent_calls": audit.recent(),
    }
