"""The result of tool_agent.prepare for one turn."""

from dataclasses import dataclass, field
from typing import Optional

PASS, CANNED, GROUNDED = "pass", "canned", "grounded"


@dataclass
class TurnPlan:
    """What the orchestrator does with one user message.

    kind       pass      nothing to add, the normal path (plus `clause` if set)
               canned    answer with `canned`, no model at all
               grounded  call the model with `context` and `clause` added
    canned     the fixed reply text (kind canned)
    context    tool data for the model (kind grounded), never logged
    clause     grounding instructions, appended to the message text
    private    True when customer data is in `context` and may not leave the
               machine (config.CUSTOMER_DATA_TO_CLOUD_LLM is false)
    mode_suffix  appended to the reply mode, for example " + SLT hub"
    status_key   i18n key of a short status line ("Checking your balance")
    tools      names of the tools that answered
    timings    {"tool_ms": int}, wall time spent waiting for tools
    expired    True when the hub login is gone and the customer must verify again
    """

    kind: str = PASS
    canned: str = ""
    context: str = ""
    clause: str = ""
    private: bool = False
    mode_suffix: str = ""
    status_key: Optional[str] = None
    tools: tuple = ()
    timings: dict = field(default_factory=lambda: {"tool_ms": 0})
    expired: bool = False
