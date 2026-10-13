"""Logging a fault by conversation: one short question at a time.

The hub's log_fault needs service_id and service_option (BB, VOICE, PEOTV,
OTHER) and takes an optional contact number and a short description. The
service id comes from show_service_detail (listofBBSService,
listofVoiceService, listofPEOService), read through the gateway. When every
slot is filled the values are read back and handed to api.propose, which shows
the confirmation. One draft per customer; it ends after 10 minutes without an
answer, on "cancel" or "stop", or when a confirmation is proposed.
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import mcp_gateway

from . import intents, store
from .models import Intercept, StartResult
from .texts import dial, lang_of, tr

DRAFT_IDLE_SECONDS = 600.0
MAX_BAD_ANSWERS = 3
_LISTS = {"BB": ("listofbbsservice",), "VOICE": ("listofvoiceservice",), "PEOTV": ("listofpeoservice",)}
_ID_FIELDS = ("serviceid", "id", "telephoneno", "number", "value", "serviceno")


@dataclass
class Draft:
    owner: str
    language: str
    step: str  # service | which | contact | nature
    updated: float
    option: str = ""
    service_id: str = ""
    choices: "list[str]" = field(default_factory=list)
    contact: str = ""
    nature: str = ""
    bad: int = 0


_drafts: "dict[str, Draft]" = {}
_lock = threading.Lock()


def clear() -> None:
    with _lock:
        _drafts.clear()


def drop(owner: str) -> None:
    with _lock:
        _drafts.pop(owner, None)


def has_draft(owner: str) -> bool:
    return _get(owner) is not None


def _get(owner: str) -> Optional[Draft]:
    with _lock:
        draft = _drafts.get(owner)
        if draft is not None and store.now() - draft.updated > DRAFT_IDLE_SECONDS:
            _drafts.pop(owner, None)
            return None
        return draft


def _find(node: Any, wanted: "tuple[str, ...]", depth: int = 0) -> "list[str]":
    ids: "list[str]" = []
    if depth > 6:
        return ids
    if isinstance(node, dict):
        for key, value in node.items():
            if re.sub(r"[^a-z]", "", str(key).lower()) in wanted and isinstance(value, list):
                for entry in value:
                    if isinstance(entry, (str, int)) and not isinstance(entry, bool) and str(entry).strip():
                        ids.append(str(entry).strip())
                    elif isinstance(entry, dict):
                        keyed = {re.sub(r"[^a-z]", "", str(k).lower()): v for k, v in entry.items()}
                        for name in _ID_FIELDS:
                            if keyed.get(name) not in (None, ""):
                                ids.append(str(keyed[name]).strip())
                                break
            elif isinstance(value, (dict, list)):
                ids.extend(_find(value, wanted, depth + 1))
    elif isinstance(node, list):
        for value in node:
            ids.extend(_find(value, wanted, depth + 1))
    return ids


def _service_ids(access, option: str) -> "list[str]":
    result = mcp_gateway.call("show_service_detail", {}, access)
    if not result.ok or not isinstance(result.data, dict):
        return []
    order = [option] if option in _LISTS else ["BB", "VOICE", "PEOTV"]
    ids: "list[str]" = []
    for name in order:
        for found in _find(result.data, _LISTS[name]):
            if found not in ids:
                ids.append(found)
    return ids


def _tail(value: str) -> str:
    return value[-3:] if len(value) >= 3 else value


def begin(access, owner: str, text: str, language: str) -> StartResult:
    lang = lang_of(language)
    draft = Draft(owner, lang, "service", store.now(), option=intents.fault_option(text))
    with _lock:
        _drafts[owner] = draft
    status = "confirm.status.fault"
    if not draft.option:
        return StartResult(tr("confirm.fault.ask_service", lang), status)
    return StartResult(_after_option(access, draft), status)


def _after_option(access, draft: Draft) -> str:
    lang = draft.language
    ids = _service_ids(access, draft.option)
    if not ids:
        drop(draft.owner)
        return tr("confirm.fault.none_found", lang, **dial())
    if len(ids) == 1:
        draft.service_id = ids[0]
        draft.step = "contact"
        return tr("confirm.fault.ask_contact", lang)
    draft.choices = ids[:5]
    draft.step = "which"
    options = "; ".join(f"{n}) {tr('confirm.ending', lang, digits=_tail(i))}" for n, i in enumerate(draft.choices, 1))
    return tr("confirm.fault.ask_which", lang, options=options)


def advance(access, owner: str, text: str, language: Optional[str]) -> Optional[Intercept]:
    draft = _get(owner)
    if draft is None:
        return None
    if language:
        draft.language = lang_of(language)
    lang = draft.language
    draft.updated = store.now()
    if intents.wants_cancel(text):
        drop(owner)
        return Intercept(tr("confirm.fault.stopped", lang), None, "cancelled")
    if draft.step == "service":
        draft.option = intents.fault_option(text)
        if not draft.option:
            return _bad(draft, "confirm.fault.ask_service")
        return Intercept(_after_option(access, draft), "confirm.status.fault", "question")
    if draft.step == "which":
        picked = _pick(draft, text)
        if not picked:
            return _bad(draft, "confirm.fault.bad_choice")
        draft.service_id, draft.step, draft.bad = picked, "contact", 0
        return Intercept(tr("confirm.fault.ask_contact", lang), None, "question")
    if draft.step == "contact":
        if intents.is_skip(text):
            draft.contact = ""
        else:
            digits = re.sub(r"\D", "", text or "")
            if not 7 <= len(digits) <= 12:
                return _bad(draft, "confirm.fault.ask_contact")
            draft.contact = digits
        draft.step, draft.bad = "nature", 0
        return Intercept(tr("confirm.fault.ask_nature", lang), None, "question")
    draft.nature = "" if intents.is_skip(text) else (text or "").strip()[:300]
    return _finish(access, draft)


def _bad(draft: Draft, key: str) -> Intercept:
    draft.bad += 1
    if draft.bad >= MAX_BAD_ANSWERS:
        drop(draft.owner)
        return Intercept(tr("confirm.fault.stopped", draft.language), None, "cancelled")
    return Intercept(tr(key, draft.language), None, "question")


def _pick(draft: Draft, text: str) -> str:
    digits = re.sub(r"\D", "", text or "")
    if digits.isdigit() and 1 <= len(digits) <= 2 and 1 <= int(digits) <= len(draft.choices):
        return draft.choices[int(digits) - 1]
    if len(digits) >= 3:
        hits = [c for c in draft.choices if c.endswith(digits)]
        if len(hits) == 1:
            return hits[0]
    return ""


def _finish(access, draft: Draft) -> Intercept:
    from . import api  # lazy: api imports this module

    lang = draft.language
    missing = tr("confirm.not_provided", lang)
    arguments = {"service_id": draft.service_id, "service_option": draft.option}
    if draft.contact:
        arguments["contact_no"] = draft.contact
    if draft.nature:
        arguments["nature_of_fault"] = draft.nature
    service = tr(f"confirm.option.{draft.option}", lang) + " (" + tr("confirm.ending", lang, digits=_tail(draft.service_id)) + ")"
    lines = [
        {"label": tr("confirm.label.service", lang), "value": service},
        {"label": tr("confirm.label.contact", lang), "value": ("*" * max(0, len(draft.contact) - 3) + draft.contact[-3:]) if draft.contact else missing},
        {"label": tr("confirm.label.problem", lang), "value": draft.nature or missing},
    ]
    drop(draft.owner)
    pending = api.propose(access, "log_fault", arguments, lines, lang, None)
    if pending is None:
        return Intercept(tr("confirm.off", lang, **dial()), None, "disabled")
    return Intercept(api.ask_text(pending, lang), "confirm.status.fault", "proposed", api.pending_for(access))
