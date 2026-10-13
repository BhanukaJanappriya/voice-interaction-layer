"""The confirmation step for actions that change an account (plan section 7).

    propose(access, tool, arguments, summary_lines, language, title) -> Pending
    pending_for(access) -> browser safe dict or None
    confirm(access, token) -> ConfirmResult
    cancel(access, token) -> ConfirmResult
    intercept(access, text, language) -> Intercept or None   (call first)
    start(access, text, language) -> StartResult or None     (call second)

Rules kept here: nothing runs without a confirm; a confirm runs the hub tool
once and never again, also not after a transport failure (no idempotency key
exists, so a repeat could buy twice); the hub's own words are the result the
customer sees; with STATE_CHANGING_TOOLS_ENABLED off no action can exist.
Every public function is blocking and never raises. Tool arguments, numbers
and hub answers are never logged.
"""

import time
from typing import Any, Optional

import config
import mcp_gateway

from . import fault, intents, packages, store
from .models import ConfirmResult, Intercept, Pending, StartResult
from .texts import dial, lang_of, tr

STATE_TOOLS = ("purchase_extra_gb", "add_vas_data_bundle", "subscribe_lte_prepaid_package", "log_fault")

_LIST_TOOL = {
    "purchase_extra_gb": "show_extra_gb_packages",
    "add_vas_data_bundle": "show_vas_data_bundle_packages",
    "subscribe_lte_prepaid_package": "show_lte_prepaid_package_list",
}


def enabled() -> bool:
    return bool(getattr(config, "STATE_CHANGING_TOOLS_ENABLED", False))


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _lines(summary_lines: Any) -> "list[dict]":
    out = []
    for line in summary_lines or []:
        if isinstance(line, dict):
            label, value = line.get("label"), line.get("value")
        else:
            label, value = line[0], line[1]
        out.append({"label": str(label), "value": str(value)})
    return out


def _view(action) -> dict:
    return {
        "id": action.token,
        "tool": action.tool,
        "title": action.title,
        "lines": [dict(line) for line in action.lines],
        "expires_in": action.remaining(),
        "state": "pending",
    }


# Propose, read, cancel


def propose(access, tool: str, arguments: dict, summary_lines, language: Optional[str], title: Optional[str]) -> Optional[Pending]:
    """Stores one pending action for the verified customer, replacing an
    earlier one. None for a guest, an unknown tool, or when changes are not
    switched on (then nothing is stored)."""
    try:
        owner = store.owner_key(access)
        if owner is None or tool not in STATE_TOOLS or not enabled():
            return None
        lang = lang_of(language)
        shown = str(title or "").strip() or tr(f"confirm.title.{tool}", lang)
        action = store.put(owner, tool, arguments if isinstance(arguments, dict) else {}, shown, _lines(summary_lines), lang)
        fault.drop(owner)
        return Pending(action.token, action.tool, action.title, [dict(line) for line in action.lines], action.remaining())
    except Exception:  # noqa: BLE001
        return None


def pending_for(access) -> Optional[dict]:
    try:
        owner = store.owner_key(access)
        action = store.current(owner) if owner else None
        return _view(action) if action else None
    except Exception:  # noqa: BLE001
        return None


def confirm(access, token: str) -> ConfirmResult:
    started = time.perf_counter()
    try:
        return _confirm(access, token, started)
    except Exception:  # noqa: BLE001  nothing is repeated, so say we cannot tell
        return ConfirmResult("failed", tr("confirm.unreachable", None, **dial()), "", _ms(started))


def _confirm(access, token: str, started: float) -> ConfirmResult:
    owner = store.owner_key(access)
    if owner is None:
        return ConfirmResult("invalid", tr("confirm.invalid"), "", _ms(started))
    if not enabled():
        store.take(owner, token, store.CANCELLED)
        return ConfirmResult("disabled", tr("confirm.disabled"), "", _ms(started))
    status, action = store.take(owner, token)
    if status == "invalid" or action is None:
        return ConfirmResult("invalid", tr("confirm.invalid"), "", _ms(started))
    lang = action.language
    if status == "expired":
        return ConfirmResult("expired", tr("confirm.expired", lang), action.tool, _ms(started))
    # The one and only call. confirmed=True is allowed here and nowhere else.
    result = mcp_gateway.call(action.tool, action.arguments, access, confirmed=True)
    ms = _ms(started)
    kind = result.kind
    if kind == "ok":
        return ConfirmResult("executed", result.text or tr("confirm.done", lang), action.tool, ms)
    if kind == "error":
        lead = tr("confirm.failed_lead", lang)
        return ConfirmResult("failed", (lead + " " + result.text).strip(), action.tool, ms)
    if kind == "reauth":
        return ConfirmResult("failed", tr("confirm.reauth", lang), action.tool, ms)
    if kind == "refused":
        return ConfirmResult("refused", tr("confirm.refused", lang), action.tool, ms)
    return ConfirmResult("failed", tr("confirm.unreachable", lang, **dial()), action.tool, ms)


def cancel(access, token: str) -> ConfirmResult:
    started = time.perf_counter()
    try:
        owner = store.owner_key(access)
        if owner is None:
            return ConfirmResult("invalid", tr("confirm.invalid"), "", _ms(started))
        status, action = store.take(owner, token, store.CANCELLED)
        if status == "ok" and action is not None:
            return ConfirmResult("cancelled", tr("confirm.cancelled", action.language), action.tool, _ms(started))
        if status == "expired" and action is not None:
            return ConfirmResult("expired", tr("confirm.expired", action.language), action.tool, _ms(started))
        return ConfirmResult("invalid", tr("confirm.invalid"), "", _ms(started))
    except Exception:  # noqa: BLE001
        return ConfirmResult("invalid", tr("confirm.invalid"), "", _ms(started))


# Spoken or typed answers


def intercept(access, text: str, language: Optional[str] = None) -> Optional[Intercept]:
    """Handles the customer's message when it answers a waiting action or an
    open fault draft. None when neither exists: the caller carries on."""
    try:
        owner = store.owner_key(access)
        if owner is None:
            return None
        action = store.current(owner)
        if action is not None:
            lang = language or action.language
            if intents.classify_reply(text) == "yes":
                done = confirm(access, action.token)
                return Intercept(done.text, "confirm.status.executing", done.kind)
            store.take(owner, action.token, store.CANCELLED)
            return Intercept(tr("confirm.cancelled", lang), None, "cancelled")
        gone = store.last_expired(owner)
        if gone is not None:
            store.forget(owner)
            if intents.classify_reply(text) in ("yes", "no"):
                return Intercept(tr("confirm.expired", language or gone.language), None, "expired")
        return fault.advance(access, owner, text, language)
    except Exception:  # noqa: BLE001
        return None


# Starting an action from the customer's words


def start(access, text: str, language: Optional[str] = None) -> Optional[StartResult]:
    try:
        owner = store.owner_key(access)
        if owner is None:
            return None  # a guest: the policy guard answers
        intent = intents.detect_request(text)
        if not intent:
            return None
        lang = lang_of(language)
        if not enabled():
            return StartResult(tr("confirm.off", lang, **dial()))
        if intent == "fault":
            return fault.begin(access, owner, text, lang)
        return _start_package(access, owner, intent, text, lang)
    except Exception:  # noqa: BLE001
        return None


def _current_package(access) -> str:
    """The customer's present package name, which the hub's list tools ask for."""
    result = mcp_gateway.call("show_gb_balance", {}, access)
    data = result.data if result.ok and isinstance(result.data, dict) else {}
    for key, value in data.items():
        if str(key).lower().replace("_", "") in ("packagename", "subscriberpackage", "package") and isinstance(value, str):
            return value[:80]
    return ""


def _option_text(items: "list[dict]", lang: str) -> str:
    parts = []
    for number, item in enumerate(items, 1):
        label = f"{number}) {item['name']}"
        if item["price"] is not None:
            label += f" ({item['price']})"
        parts.append(label)
    return "; ".join(parts)


def _start_package(access, owner: str, intent: str, text: str, lang: str) -> Optional[StartResult]:
    service = mcp_gateway.service_type(access)
    if service == "lte_prepaid":
        tool = "subscribe_lte_prepaid_package"
        low = intents.normalise(text)
        kind = "Add-ons" if any(w in low for w in ("add-on", "addon", "add on", "extra", "additional", "අමතර", "கூடுதல்")) else "Main Packages"
        listing = mcp_gateway.call(_LIST_TOOL[tool], {"package_type": kind}, access)
    elif service == "postpaid":
        if intent == "package":
            return None
        tool = "add_vas_data_bundle" if intent == "vas" else "purchase_extra_gb"
        listing = mcp_gateway.call(_LIST_TOOL[tool], {"package_name": _current_package(access)}, access)
    else:
        return StartResult(tr("confirm.pkg.unknown_service", lang, **dial()))
    status = "confirm.status.packages"
    if listing.kind == "reauth":
        return StartResult(tr("confirm.reauth", lang), status)
    items = packages.parse(listing) if listing.ok else []
    if not items:
        return StartResult(tr("confirm.pkg.unreadable", lang), status)
    how, chosen = packages.match(items, text)
    if how == "none":
        return StartResult(tr("confirm.pkg.none", lang), status)
    if how == "several":
        return StartResult(tr("confirm.pkg.which", lang, options=_option_text(chosen, lang)), status)
    item = chosen[0]
    if tool == "subscribe_lte_prepaid_package":
        arguments: dict = {"product_id": str(item["id"]), "offer_name": item["name"], "operation_type": "ADD_OFFERING"}
    else:
        number = packages.int_id(item["id"])
        if number is None:
            return StartResult(tr("confirm.pkg.unreadable", lang), status)
        arguments = {"package_id": number}
    missing = tr("confirm.not_provided", lang)
    lines = [
        {"label": tr("confirm.label.package", lang), "value": item["name"]},
        {"label": tr("confirm.label.price", lang), "value": str(item["price"]) if item["price"] is not None else missing},
        {"label": tr("confirm.label.validity", lang), "value": str(item["validity"]) if item["validity"] is not None else missing},
        {"label": tr("confirm.label.connection", lang), "value": tr("confirm.ending", lang, digits=owner[-3:])},
    ]
    pending = propose(access, tool, arguments, lines, lang, None)
    if pending is None:
        return StartResult(tr("confirm.off", lang, **dial()), status)
    return StartResult(ask_text(pending, lang), status, pending)


def ask_text(pending: Pending, lang: str) -> str:
    """The sentence that states exactly what will happen and asks for a yes."""
    details = "; ".join(f"{line['label']}: {line['value']}" for line in pending.lines)
    return tr("confirm.ask", lang, action=pending.title, details=details, seconds=pending.expires_in)
