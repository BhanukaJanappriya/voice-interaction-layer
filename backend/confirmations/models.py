"""Small result types shared by the package. Plain data, no behavior."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Pending:
    token: str
    tool: str
    title: str
    lines: "list[dict]"
    expires_in: int


@dataclass
class ConfirmResult:
    kind: str  # executed | failed | expired | invalid | disabled | refused | cancelled
    text: str = ""
    tool: str = ""
    ms: int = 0


@dataclass
class Intercept:
    text: str
    status_key: Optional[str] = None
    kind: str = ""  # executed | failed | cancelled | expired | disabled | refused | question | proposed
    confirmation: Optional[dict] = field(default=None)  # the browser view when a new action waits


@dataclass
class StartResult:
    text: str
    status_key: Optional[str] = None
    pending: Optional[Pending] = None
