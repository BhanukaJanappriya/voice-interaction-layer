"""Tools for a Live session: what the model may see and what happens when it calls one.

declarations(...)  mcp_gateway.tool_schemas -> google.genai FunctionDeclaration list
build_tools(...)   the same wrapped in a types.Tool list for LiveConnectConfig
ToolExecutor       async execute(name, args) used by GeminiLiveSession

Rules kept here:
  * the model never gets a telephone number or token parameter (the gateway
    injects the verified number; any such property is removed again here);
  * a call for a tool that was not declared, or that the gateway refuses, is
    answered with {"error": ...} and never raises;
  * a state changing tool never reaches the hub from here: it is turned into a
    pending confirmation (confirmations.propose) and the model gets
    {"status": "needs_confirmation", "say": ...}. Only the confirmations module
    runs it, after the customer's yes.
Arguments, numbers and hub answers are never logged.
"""

import asyncio
import re
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from google.genai import types

_FORBIDDEN_PARAM = re.compile(r"phone|telephone|msisdn|token|otp|password|secret|session_id", re.I)


# Schema conversion


def _clean_schema(node: Any, top: bool = False) -> dict:
    """A JSON schema reduced to what the Live API's Schema accepts."""
    if not isinstance(node, dict):
        return {"type": "string"}
    if "anyOf" in node or "oneOf" in node:
        options = [o for o in (node.get("anyOf") or node.get("oneOf") or []) if isinstance(o, dict)]
        real = [o for o in options if o.get("type") != "null"]
        picked = _clean_schema(real[0] if real else {"type": "string"})
        if len(real) != len(options):
            picked["nullable"] = True
        if node.get("description") and "description" not in picked:
            picked["description"] = str(node["description"])
        return picked
    out: dict = {}
    kind = node.get("type")
    if isinstance(kind, list):
        non_null = [k for k in kind if k != "null"]
        if len(non_null) != len(kind):
            out["nullable"] = True
        kind = non_null[0] if non_null else "string"
    if kind not in ("string", "number", "integer", "boolean", "array", "object"):
        kind = "object" if top or "properties" in node else "string"
    out["type"] = kind
    if isinstance(node.get("description"), str):
        out["description"] = node["description"][:1000]
    if isinstance(node.get("enum"), list) and kind == "string":
        out["enum"] = [str(v) for v in node["enum"]]
    if isinstance(node.get("format"), str) and kind in ("string", "number", "integer"):
        out["format"] = node["format"]
    if kind == "object":
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        kept = {k: _clean_schema(v) for k, v in props.items() if not _FORBIDDEN_PARAM.search(str(k))}
        out["properties"] = kept
        required = [r for r in (node.get("required") or []) if r in kept]
        if required:
            out["required"] = required
    if kind == "array":
        out["items"] = _clean_schema(node.get("items") or {"type": "string"})
    return out


def declarations(schemas: Iterable[dict]) -> List[types.FunctionDeclaration]:
    """FunctionDeclarations from mcp_gateway.tool_schemas() items."""
    out: List[types.FunctionDeclaration] = []
    for item in schemas or []:
        name = str(item.get("name") or "")
        if not name:
            continue
        params = _clean_schema(item.get("input_schema") or {}, top=True)
        if params.get("type") != "object":
            params = {"type": "object", "properties": {}}
        declaration = types.FunctionDeclaration(
            name=name,
            description=str(item.get("description") or "")[:1500],
            parameters=types.Schema.model_validate(params) if params.get("properties") else None,
        )
        out.append(declaration)
    return out


def declared_names(tools: Iterable[types.Tool]) -> List[str]:
    return [d.name for t in tools or [] for d in (t.function_declarations or []) if d.name]


def build_tools(access, service_type: Optional[str] = None, include_state_changing: bool = False) -> List[types.Tool]:
    """What the model may see for this caller. Blocking (one hub list call on a
    cache miss); call it on a thread from async code. A guest gets exactly one
    declaration, search_knowledgebase_tool."""
    import mcp_gateway

    schemas = mcp_gateway.tool_schemas(access, service_type, include_state_changing)
    decls = declarations(schemas)
    return [types.Tool(function_declarations=decls)] if decls else []


# Executor

ToolExecutorFn = Callable[[str, dict], Awaitable[dict]]


def _humanize(key: str) -> str:
    return str(key).replace("_", " ").strip().capitalize()


def _summary_lines(arguments: dict) -> List[dict]:
    lines = []
    for key, value in (arguments or {}).items():
        if _FORBIDDEN_PARAM.search(str(key)) or isinstance(value, (dict, list)):
            continue
        lines.append({"label": _humanize(key), "value": str(value)[:80]})
    return lines


def _clean_args(arguments: Any) -> dict:
    if not isinstance(arguments, dict):
        return {}
    return {str(k): v for k, v in arguments.items() if not _FORBIDDEN_PARAM.search(str(k))}


class ToolExecutor:
    """Runs the model's tool calls for ONE caller (one AccessContext).

    `allowed` is the set of names that were declared to the model; when given,
    any other name is refused before the gateway is asked (the gateway checks
    the tier again; both must agree). `on_confirmation(view)` is called with
    the browser safe pending action so the page can show its modal.
    """

    def __init__(
        self,
        access,
        language: Optional[str] = None,
        allowed: Optional[Iterable[str]] = None,
        on_confirmation: Optional[Callable[[dict], Any]] = None,
        gateway=None,
        confirmations=None,
        matrix=None,
    ):
        from access import GUEST

        self.access = access if access is not None else GUEST
        self.language = language
        self.allowed = frozenset(allowed) if allowed is not None else None
        self.on_confirmation = on_confirmation
        self._gateway = gateway
        self._confirmations = confirmations
        self._matrix = matrix

    # lazy so importing this module never needs the hub stack
    def _gw(self):
        if self._gateway is None:
            import mcp_gateway

            self._gateway = mcp_gateway
        return self._gateway

    def _conf(self):
        if self._confirmations is None:
            import confirmations

            self._confirmations = confirmations
        return self._confirmations

    def _is_state_changing(self, name: str) -> bool:
        if self._matrix is None:
            from mcp_gateway import access_matrix

            self._matrix = access_matrix.load()
        return bool(self._matrix.is_state_changing(name))

    @classmethod
    def for_tools(cls, access, tools: Iterable[types.Tool], language: Optional[str] = None, **kw) -> "ToolExecutor":
        return cls(access, language, allowed=declared_names(tools), **kw)

    async def __call__(self, name: str, args: dict) -> dict:
        return await self.execute(name, args, self.access)

    async def execute(self, name: str, args: Optional[dict], access=None) -> dict:
        """Never raises."""
        who = access if access is not None else self.access
        try:
            return await self._execute(str(name or ""), args, who)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return {"error": "tool_failed"}

    async def _execute(self, name: str, args: Optional[dict], access) -> dict:
        if not name or (self.allowed is not None and name not in self.allowed):
            return {"error": "unknown_tool"}
        clean = _clean_args(args)
        if self._is_state_changing(name):
            return await self._propose(name, clean, access)
        result = await asyncio.to_thread(self._gw().call, name, clean, access)
        return self._shape(result)

    @staticmethod
    def _shape(result) -> dict:
        kind = getattr(result, "kind", "")
        if kind == "ok":
            return {"result": result.text}
        if kind == "refused":
            return {"error": "not_allowed"}
        if kind == "reauth":
            return {"error": "reauth", "note": "The login has ended. Ask the caller to verify again with a one time code."}
        if kind == "error":
            return {"error": "tool_error", "message": str(getattr(result, "text", "") or "")[:400]}
        return {"error": str(getattr(result, "reason", "") or "unavailable")[:40]}

    async def _propose(self, name: str, arguments: dict, access) -> dict:
        conf = self._conf()
        pending = await asyncio.to_thread(
            conf.propose, access, name, arguments, _summary_lines(arguments), self.language, None
        )
        if pending is None:
            return {"error": "not_allowed"}
        from confirmations.api import ask_text
        from confirmations.texts import lang_of

        say = ask_text(pending, lang_of(self.language))
        if self.on_confirmation is not None:
            try:
                view = await asyncio.to_thread(conf.pending_for, access)
                if view:
                    outcome = self.on_confirmation(view)
                    if asyncio.iscoroutine(outcome):
                        await outcome
            except Exception:  # noqa: BLE001  the page can still confirm by voice
                pass
        return {"status": "needs_confirmation", "say": say}


def make_executor(access, tools: Iterable[types.Tool], language: Optional[str] = None, on_confirmation=None) -> ToolExecutor:
    return ToolExecutor.for_tools(access, tools, language, on_confirmation=on_confirmation)
