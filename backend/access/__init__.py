"""Access control: guests chat and get public information, customer data
needs a verified (OTP) session.

Public surface:
  AccessContext, GUEST          who is asking (context.py)
  evaluate(text, language, ctx) policy guard before any model (guard.py)
  blocked_reply(language)       canned translated refusal (messages.py)
  authorize_tool, bind_arguments, AccessDenied   hub tool gate (tool_gate.py)
  level_clause(ctx, language)   system prompt clause (prompt.py)

Self check: python -m access   (from the backend directory)
"""

from .context import GUEST, AccessContext
from .guard import Decision, evaluate
from .messages import blocked_reply, expired_reply
from .policy import PolicyError
from .prompt import level_clause
from .tool_gate import AccessDenied, authorize_tool, bind_arguments

__all__ = [
    "GUEST", "AccessContext", "Decision", "evaluate", "blocked_reply", "expired_reply", "PolicyError",
    "level_clause", "AccessDenied", "authorize_tool", "bind_arguments",
]
