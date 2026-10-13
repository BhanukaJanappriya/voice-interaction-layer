"""Reading the hub's package lists and choosing the one the customer named.

The real answer shapes are not observed yet, so everything here is defensive:
it looks for lists of dicts anywhere in the answer and reads fields by name
with case, underscores and spaces ignored. A field that is absent stays None;
nothing is ever invented or guessed into a summary.
"""

import difflib
import json
import re
from typing import Any, Optional

from . import intents

_ID_KEYS = ("packageid", "id", "offeringid", "productid", "pkgid", "vasid")
_NAME_KEYS = ("packagename", "name", "offeringname", "packagedescription", "description", "title")
_PRICE_KEYS = ("pricelkrwithtax", "pricewithtax", "price", "amount", "charge", "cost", "totalprice")
_VALID_KEYS = ("validity", "validityperiod", "validitydays", "validfor", "duration", "validtill")
_VOLUME_KEYS = ("datavolumegb", "volumegb", "volume", "datavolume", "size", "gb", "quota")

_GENERIC = {
    "buy", "purchase", "get", "add", "want", "need", "order", "subscribe", "activate", "please", "pls",
    "a", "an", "the", "me", "my", "i", "to", "for", "of", "on", "package", "packages", "pack", "plan",
    "data", "extra", "gb", "bundle", "vas", "addon", "add-on", "additional", "can", "could", "would",
    "like", "one", "this", "that", "it", "ekak", "eka", "ganna", "ganin", "ona", "oney", "karanna", "mata", "mama", "one", "main", "and", "with",
}


def _key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _field(item: dict, wanted: "tuple[str, ...]") -> Any:
    keyed = {_key(k): v for k, v in item.items()}
    for name in wanted:
        value = keyed.get(name)
        if value is not None and value != "" and not isinstance(value, (dict, list)):
            return value
    return None


def _walk(node: Any, out: "list[dict]", depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(node, dict):
        if _field(node, _ID_KEYS) is not None and _field(node, _NAME_KEYS) is not None:
            out.append(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _walk(value, out, depth + 1)


def parse(result) -> "list[dict]":
    """[{id, name, price, validity, volume}] from a gateway result, or []."""
    data: Any = getattr(result, "data", None)
    if data is None:
        text = (getattr(result, "text", "") or "").strip()
        if text[:1] in "[{":
            try:
                data = json.loads(text)
            except ValueError:
                data = None
    found: "list[dict]" = []
    _walk(data, found)
    items, seen = [], set()
    for raw in found:
        item = {
            "id": _field(raw, _ID_KEYS),
            "name": str(_field(raw, _NAME_KEYS)),
            "price": _field(raw, _PRICE_KEYS),
            "validity": _field(raw, _VALID_KEYS),
            "volume": _field(raw, _VOLUME_KEYS),
        }
        marker = (str(item["id"]), item["name"])
        if marker in seen:
            continue
        seen.add(marker)
        items.append(item)
    return items


def _words(text: Any) -> "list[str]":
    spaced = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", re.sub(r"([a-zA-Z])(\d)", r"\1 \2", str(text or "")))
    return intents.tokens(spaced)


def _meaningful(text: Any) -> "list[str]":
    """Latin letters and digits only: package names are English, so Sinhala
    and Tamil filler words around them are ignored."""
    return [w for w in _words(text) if w.isascii() and w not in _GENERIC]


def match(items: "list[dict]", wanted_text: str) -> "tuple[str, list[dict]]":
    """("one", [item]) a clear match; ("several", up to 3 items) the customer
    must choose; ("none", []) nothing fits. Nothing named at all with one
    package on offer is that package; with several it is "several"."""
    if not items:
        return "none", []
    asked = _meaningful(wanted_text)
    numbers = [w for w in asked if w.isdigit()]
    if not asked:
        if len(items) == 1:
            return "one", items
        return "several", items[:3]
    if numbers:
        pool = []
        for item in items:
            have = set(_words(item["name"])) | set(_words(item["volume"] if item["volume"] is not None else ""))
            if all(n in have for n in numbers):
                pool.append(item)
        items = pool
        if not items:
            return "none", []
    covered = []
    for item in items:
        own = set(_meaningful(item["name"]))
        have = own | set(_words(item["volume"] if item["volume"] is not None else ""))
        if all(w in have for w in asked):
            covered.append((own == set(asked), item))
    if covered:
        exact = [item for same, item in covered if same]
        pick = exact if exact else [item for _, item in covered]
        return ("one", pick) if len(pick) == 1 else ("several", pick[:3])
    scored = []
    joined = " ".join(asked)
    for item in items:
        ratio = difflib.SequenceMatcher(None, joined, " ".join(_meaningful(item["name"]))).ratio()
        scored.append((ratio, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored and scored[0][0] >= 0.75:
        top = [item for ratio, item in scored if ratio >= scored[0][0] - 0.01]
        return ("one", top) if len(top) == 1 else ("several", top[:3])
    return "none", []


def int_id(value: Any) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        text = str(value).strip()
        return int(text) if re.fullmatch(r"\d{1,9}", text) else None
    except (TypeError, ValueError):
        return None
