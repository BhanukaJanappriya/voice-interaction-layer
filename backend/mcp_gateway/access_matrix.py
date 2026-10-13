"""Which hub tools each kind of session may use (config/tool_access.yaml).

Tiers: guest, authenticating, authenticated, diagnostic, never.

The authenticated tier is narrowed by service type: a tool named under a
service type in the file is offered only to that type, a tool named under no
service type is common to all. The type "unknown" gets the common tools only.

Fails closed. A missing or broken file leaves guests with the knowledge
search and nothing else, and one warning is logged (no detail: the file may
sit next to things that should not be in a log).
"""

import threading
from typing import Iterable, Optional

import config

TIERS = ("guest", "authenticating", "authenticated", "diagnostic", "never")
SERVICE_TYPES = ("postpaid", "lte_prepaid", "unknown")
FALLBACK_GUEST = ("search_knowledgebase_tool",)
LATENCY_TOOL = "get_function_latencies"


class Matrix:
    def __init__(
        self,
        tiers: Optional[dict] = None,
        service_types: Optional[dict] = None,
        state_changing: Optional[Iterable[str]] = None,
        loaded: bool = True,
    ):
        tiers = tiers or {}
        self._tiers = {tier: tuple(tiers.get(tier) or ()) for tier in TIERS}
        self._service_types = {name: frozenset(tools or ()) for name, tools in (service_types or {}).items()}
        self._restricted = frozenset().union(*self._service_types.values()) if self._service_types else frozenset()
        self._state_changing = frozenset(state_changing or ())
        self.loaded = loaded

    # Lookups

    def never(self, tool: str) -> bool:
        return tool in self._tiers["never"]

    def is_state_changing(self, tool: str) -> bool:
        return tool in self._state_changing

    def restricted(self, tool: str) -> bool:
        """True when the tool is offered only to some service types."""
        return tool in self._restricted

    def in_tier(self, tool: str, tier: str) -> bool:
        """In the tier's list, ignoring service types and the latency switch."""
        if self.never(tool):
            return False
        return tool in self._tiers.get(tier, ())

    def _type_allows(self, tool: str, service_type: str) -> bool:
        if tool not in self._restricted:
            return True
        return tool in self._service_types.get(service_type, frozenset())

    def tool_names_for(self, tier: str, service_type: str = "unknown", expose_latency: Optional[bool] = None) -> list:
        if tier not in self._tiers:
            return []
        if expose_latency is None:
            expose_latency = bool(getattr(config, "EXPOSE_LATENCY_TOOL", False))
        names = [t for t in self._tiers[tier] if not self.never(t)]
        if tier == "authenticated":
            names = [t for t in names if self._type_allows(t, service_type)]
            if expose_latency and LATENCY_TOOL in self._tiers["diagnostic"] and LATENCY_TOOL not in names:
                names.append(LATENCY_TOOL)
        return names

    def allowed(self, tool: str, tier: str, service_type: str = "unknown") -> bool:
        return tool in self.tool_names_for(tier, service_type)


def _fail_closed() -> Matrix:
    return Matrix(tiers={"guest": list(FALLBACK_GUEST), "authenticated": list(FALLBACK_GUEST)}, loaded=False)


def _names(value) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError("a tool list must be a list of names")
    return [v.strip() for v in value]


def _parse(document) -> Matrix:
    if not isinstance(document, dict) or not isinstance(document.get("tiers"), dict):
        raise ValueError("no tiers")
    raw_tiers = document["tiers"]
    tiers = {tier: _names(raw_tiers.get(tier)) for tier in TIERS}
    if not tiers["guest"]:
        raise ValueError("no guest tools")
    raw_types = document.get("service_types") or {}
    if not isinstance(raw_types, dict):
        raise ValueError("service_types")
    types = {str(name): _names(tools) for name, tools in raw_types.items()}
    return Matrix(tiers=tiers, service_types=types, state_changing=_names(document.get("state_changing")))


_matrix: Optional[Matrix] = None
_lock = threading.Lock()


def load(path: Optional[str] = None) -> Matrix:
    """The matrix from the file, cached. An explicit path is read fresh each
    time and not cached (for tests); reload() drops the cache."""
    global _matrix
    if path is not None:
        return _read(path)
    with _lock:
        if _matrix is None:
            _matrix = _read(getattr(config, "TOOL_ACCESS_PATH", ""))
        return _matrix


def reload() -> Matrix:
    global _matrix
    with _lock:
        _matrix = None
    return load()


def _read(path: str) -> Matrix:
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as handle:
            return _parse(yaml.safe_load(handle))
    except Exception:  # noqa: BLE001  (missing, unreadable, invalid: all fail closed)
        print("MCP gateway: the tool access file could not be used; guests get the knowledge search only.")
        return _fail_closed()
