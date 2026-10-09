"""Gate for MCP hub tool calls (ADR 3.5).

A tool call is allowed when the tool is in the policy public_tools, or the
caller is verified AND the telephone_no argument is the caller's own number
(compared in +94 form). bind_arguments forces telephone_no from the
context, so the model can never choose another customer's number.

Note: the OTP login tools (validate_customer_and_send_otp and friends) are
customer tools here. The OTP_BACKEND=hub login path is not a model tool
call and must call the hub without an access context.
"""

import re
from typing import Any, Mapping, Optional

from .context import AccessContext
from .policy import get_policy

PHONE_ARG = "telephone_no"


class AccessDenied(Exception):
    def __init__(self, tool: str, reason: str):
        super().__init__(f"tool {tool!r} denied: {reason}")
        self.tool = tool
        self.reason = reason


def normalize_phone(value: Any) -> Optional[str]:
    """+94XXXXXXXXX for 0112345678, 94112345678, 0094112345678, +94 11 234 5678;
    None when it does not look like a Sri Lankan number."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^\d]", "", value)
    if digits.startswith("0094"):
        digits = digits[4:]
    elif digits.startswith("94") and len(digits) == 11:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = digits[1:]
    if len(digits) != 9 or digits[0] == "0":
        return None
    return "+94" + digits


def is_public_tool(name: str) -> bool:
    return name in get_policy().public_tools


def authorize_tool(name: str, arguments: Optional[Mapping[str, Any]], ctx: Optional[AccessContext]) -> None:
    """Returns None when allowed, raises AccessDenied otherwise."""
    if is_public_tool(name):
        return
    if ctx is None or not ctx.is_verified:
        raise AccessDenied(name, "customer tools need a verified session")
    own = normalize_phone(ctx.phone_e164)
    if own is None:
        raise AccessDenied(name, "the verified session has no valid phone number")
    asked = normalize_phone((arguments or {}).get(PHONE_ARG))
    if asked is None:
        raise AccessDenied(name, f"{PHONE_ARG} is missing or invalid")
    if asked != own:
        raise AccessDenied(name, f"{PHONE_ARG} is not the verified customer's number")


def bind_arguments(name: str, arguments: Optional[Mapping[str, Any]], ctx: Optional[AccessContext]) -> dict:
    """A copy of the arguments with telephone_no forced from the context for
    customer tools. Raises AccessDenied for a guest on a customer tool."""
    bound = dict(arguments or {})
    if is_public_tool(name):
        return bound
    if ctx is None or not ctx.is_verified or normalize_phone(ctx.phone_e164) is None:
        raise AccessDenied(name, "customer tools need a verified session")
    bound[PHONE_ARG] = normalize_phone(ctx.phone_e164)
    return bound
