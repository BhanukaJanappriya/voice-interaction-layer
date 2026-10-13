"""Read only SLT account data for a verified customer, through the MCP hub.

    from customer_service import fetch, system_clause, canned
    outcome = fetch(text, language, access)      # blocking, never raises
    outcome.kind: "none" | "data" | "reauth" | "unavailable"

Only the tools in tools.ALLOWED_TOOLS are ever called (plan 4.2). Nothing about
a customer is logged. Self check: python -m customer_service (from backend/).
"""

from .intent import Intent, detect, wants_change
from .messages import canned, system_clause
from .service import CustomerOutcome, clear_cache, fetch, plan
from .tools import ALLOWED_TOOLS, REFUSED_TOOLS, ToolCall, ToolRefused, require_allowed

__all__ = [
    "fetch", "plan", "CustomerOutcome", "ToolCall", "Intent", "detect", "wants_change", "canned", "system_clause",
    "ALLOWED_TOOLS", "REFUSED_TOOLS", "ToolRefused", "require_allowed", "clear_cache",
]
