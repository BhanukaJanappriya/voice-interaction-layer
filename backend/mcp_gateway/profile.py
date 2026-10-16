"""The customer's name as the hub holds it.

verify_otp_and_authenticate returns no name (subscriber_id, account_number, package, privileges
only; checked live), so after a sign in the name is looked for in the read only customer tools'
answers: the first of a few tools whose answer carries a name field wins. Which tool and which
field the hub uses was NOT confirmed against the real hub (the one test login could not be
finished), so the lookup reads any field that looks like the customer's own name and ignores the
ones that name a package, a service, a bank, a city and the like. Best effort and bounded: a
missing name only means the customer is greeted by the name typed in the wizard or neutrally.

Nothing here logs or returns anything but the cleaned name.
"""

import json
import re
import time
import unicodedata
from typing import Any, Optional

# Read only customer tools tried in this order, one call each, within one overall budget.
TOOLS = ("show_service_detail", "show_bill_history", "show_last_payment", "show_gb_balance")

_NAME_KEY = re.compile(r"(name|fullname)$")
# A key or any ancestor key with one of these words does not name the person.
_NOT_A_PERSON = re.compile(
    r"package|service|offer|product|plan|bundle|channel|fault|status|payment|bank|city|address|device|handset|"
    r"tariff|category|file|menu|branch|agent|dealer|operator|app|ticket|type|option|vas|reload|recipient|payee|biller",
    re.IGNORECASE,
)
_PERSON_KEY = re.compile(r"(customer|subscriber|account|owner|holder|user|full|contact|cus|cust|first|last|sur)", re.IGNORECASE)


def _name_chars(text: str) -> bool:
    """Letters in any script (Sinhala and Tamil vowel signs are marks, not letters), spaces, dots,
    apostrophes, hyphens and the joiners Sinhala needs; at least two letters."""
    letters = 0
    for ch in text:
        category = unicodedata.category(ch)
        if category.startswith("L"):
            letters += 1
        elif not (category.startswith("M") or ch in " .'-\u2019\u200c\u200d"):
            return False
    return letters >= 2


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip(" ,;")
    if not 2 <= len(text) <= 60 or "@" in text or any(ch.isdigit() for ch in text):
        return None
    if not _name_chars(text) or re.search(r"(?i)\b(ltd|pvt|limited|plc|slt|mobitel|package|null|none|unknown|n/a)\b", text):
        return None
    return text.title() if text.isupper() or text.islower() else text


def _walk(node: Any, path: tuple, depth: int, out: list) -> None:
    if depth > 4:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            lowered = re.sub(r"[^a-z]", "", key_text.lower())
            here = path + (key_text,)
            if isinstance(value, str) and _NAME_KEY.search(lowered):
                if _NOT_A_PERSON.search(" ".join(here)):
                    continue
                name = _clean(value)
                if name:
                    score = 3 if _PERSON_KEY.search(lowered) and lowered not in ("firstname", "lastname", "surname") else 1
                    out.append((score, len(out), lowered, name))
            elif isinstance(value, (dict, list)):
                _walk(value, here, depth + 1, out)
    elif isinstance(node, list):
        for item in node[:5]:
            _walk(item, path, depth + 1, out)


def extract_name(data: Any) -> Optional[str]:
    """The best person name field in a parsed tool answer, or None."""
    found: list = []
    _walk(data, (), 0, found)
    if not found:
        return None
    best = max(found, key=lambda item: (item[0], -item[1]))
    if best[0] == 1 and best[2] in ("firstname", "lastname", "surname"):
        first = next((n for s, _i, k, n in found if k == "firstname"), None)
        last = next((n for s, _i, k, n in found if k in ("lastname", "surname")), None)
        return " ".join(part for part in (first, last) if part) or best[3]
    return best[3]


# What the lookup has learned, so a hub that never gives a name does not slow every sign in: the tool that
# gave a name is asked first next time, and after MISS_LIMIT sign ins in a row with no name anywhere the
# lookup rests for REST_SECONDS and then tries once more.
MISS_LIMIT = 2
REST_SECONDS = 1800.0
_state = {"good_tool": None, "misses": 0, "rest_until": 0.0}


def reset() -> None:
    _state.update(good_tool=None, misses=0, rest_until=0.0)


def fetch_name(session, national: str, budget: float, clock=time.monotonic) -> Optional[str]:
    """Asks the signed in session's read only tools, one after another, for the customer's name."""
    if clock() < _state["rest_until"]:
        return None
    deadline = clock() + max(budget, 0.0)
    order = sorted(TOOLS, key=lambda tool: 0 if tool == _state["good_tool"] else 1)
    for tool in order:
        left = deadline - clock()
        if left <= 0.5:
            break
        try:
            output = session.call(tool, {"telephone_no": national}, timeout=min(left, 4.0))
        except Exception:  # noqa: BLE001  a failing tool only means no name from it
            continue
        try:
            data = json.loads(getattr(output, "text", "") or "")
        except ValueError:
            continue
        name = extract_name(data)
        if name:
            _state.update(good_tool=tool, misses=0)
            return name
    _state["misses"] += 1
    if _state["misses"] >= MISS_LIMIT:
        _state.update(misses=0, rest_until=clock() + REST_SECONDS)
    return None
