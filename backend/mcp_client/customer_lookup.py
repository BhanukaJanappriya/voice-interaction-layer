"""Customer lookup through a hub tool, for the verification wizard.

Implements the CustomerLookup protocol of backend/verification/lookup.py
(find and get) on top of ONE hub tool named by MCP_HUB_CUSTOMER_LOOKUP_TOOL.

The real hub has no such tool today (see docs/MCP_HUB.md), so with
CUSTOMER_DATA_SOURCE=mcp_hub and the setting empty, find() does not answer
"no such customer", which would make every real customer look invalid. It
raises CustomerLookupUnavailable, and the wizard service is expected to turn
that into the "unavailable" reply (see the diff in the report).

Contract this class expects from the lookup tool (the request to make of the
hub administrator): input telephone_no (a string, the landline); output
JSON (structured or text) with found, display_name, mobile, email and
preferred_language, or found false when the number is not a customer. Field
names are matched loosely (mcp_client.tools.parse_customer). Only those
fields are kept; everything else the hub sends is dropped here.

The verification package is imported lazily so this module can be imported
without the database layer.
"""

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any, Optional

import config
from mcp_client.errors import HubError

_LANGUAGE_KEYS = ("preferred_language", "language", "lang", "locale")
_CACHE_SIZE = 512


class CustomerLookupUnavailable(Exception):
    """The lookup could not be answered: no lookup tool configured or listed,
    the hub is down or paused, or its answer cannot be read. reason is one of
    no_lookup_tool, tool_not_listed, no_argument, hub_error, unreadable_answer.
    Not the same as "no such customer" (find returns None for that)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"customer lookup unavailable: {reason}" + (f" ({detail})" if detail else ""))
        self.reason = reason


def _explicit_not_found(structured: Any, text: str) -> bool:
    candidates = []
    if isinstance(structured, dict):
        candidates.append(structured)
    if text and text.strip()[:1] == "{":
        try:
            candidates.append(json.loads(text))
        except ValueError:
            pass
    for item in candidates:
        lowered = {str(k).lower(): v for k, v in item.items()} if isinstance(item, dict) else {}
        if lowered.get("found") is False or lowered.get("exists") is False:
            return True
    return False


class HubCustomerLookup:
    def __init__(self, tool_name: Optional[str] = None, phone_format: str = "e164"):
        self._tool_name = tool_name
        self.phone_format = phone_format  # "e164" (+94112345678) or "national" (0112345678)
        self._lock = threading.Lock()
        self._by_id: "OrderedDict[int, Any]" = OrderedDict()

    @property
    def tool_name(self) -> str:
        return (self._tool_name if self._tool_name is not None else config.MCP_HUB_CUSTOMER_LOOKUP_TOOL or "").strip()

    def available(self) -> bool:
        """A lookup tool is named. Whether the hub lists it is learnt on the first find."""
        return bool(self.tool_name)

    def _number(self, phone_e164: str) -> str:
        digits = phone_e164.lstrip("+")
        if self.phone_format == "national" and digits.startswith("94"):
            return "0" + digits[2:]
        return "+" + digits if self.phone_format == "e164" else digits

    def find(self, phone_e164: str):
        name = self.tool_name
        if not name:
            raise CustomerLookupUnavailable("no_lookup_tool")
        from mcp_client import tools

        try:
            hub = tools.get_hub()
            budget = tools._budget()
            listed = tools._runner.run(hub.list_tools(budget=budget), timeout=budget + 1.0)
            tool = next((t for t in listed if t.name == name), None)
            if tool is None:
                raise CustomerLookupUnavailable("tool_not_listed", name)
            argument = tool.string_argument(tools._PHONE_ARGUMENTS)
            if argument is None:
                raise CustomerLookupUnavailable("no_argument", name)
            output = tools.call_tool_sync(name, {argument: self._number(phone_e164)}, budget=budget)
        except HubError as exc:
            raise CustomerLookupUnavailable("hub_error", exc.reason) from None
        except TimeoutError:
            raise CustomerLookupUnavailable("hub_error", "timeout") from None
        parsed = tools.parse_customer(output.structured, output.text)
        if parsed is None:
            if _explicit_not_found(output.structured, output.text) or not (output.text or output.structured):
                return None
            raise CustomerLookupUnavailable("unreadable_answer", name)
        record = self._record(phone_e164, parsed, output)
        return record

    def _record(self, phone_e164: str, parsed: dict, output) -> Any:
        from verification.lookup import CustomerRecord
        from verification.namematch import normalize_name

        language = None
        raw = output.structured if isinstance(output.structured, (dict, list)) else None
        if raw is None and output.text.strip()[:1] in "{[":
            try:
                raw = json.loads(output.text)
            except ValueError:
                raw = None
        record = tools_find_record(raw)
        if record is not None:
            from mcp_client.tools import _first

            value = _first(record, _LANGUAGE_KEYS)
            if isinstance(value, str) and value.strip().lower()[:2] in ("en", "si", "ta"):
                language = value.strip().lower()[:2]
        display = parsed.get("display_name") or ""
        # A stable positive id from the number, so a ticket made now can be
        # resolved by get() later in this process. Hub records carry no id.
        record_id = int(hashlib.sha256(phone_e164.encode()).hexdigest()[:12], 16)
        found = CustomerRecord(
            id=record_id,
            phone_e164=phone_e164,
            display_name=display,
            name_normalized=normalize_name(display) if display else "",
            mobile_e164=parsed.get("mobile"),
            email=parsed.get("email"),
            preferred_language=language,
        )
        with self._lock:
            self._by_id[record_id] = found
            self._by_id.move_to_end(record_id)
            while len(self._by_id) > _CACHE_SIZE:
                self._by_id.popitem(last=False)
        return found

    def get(self, record_id: int):
        """From the records this process found; None after a restart (the
        challenge then fails closed and the person starts again)."""
        with self._lock:
            return self._by_id.get(record_id)


def tools_find_record(value: Any) -> Optional[dict]:
    from mcp_client.tools import _find_record

    return _find_record(value) if value is not None else None


def select_customer_lookup(source: Optional[str] = None, local_factory=None):
    """CUSTOMER_DATA_SOURCE: "local" (default) returns the local directory,
    "mcp_hub" returns HubCustomerLookup. Anything else is local."""
    chosen = (source if source is not None else getattr(config, "CUSTOMER_DATA_SOURCE", "local")).strip().lower()
    if chosen == "mcp_hub":
        return HubCustomerLookup()
    if local_factory is not None:
        return local_factory()
    from verification.lookup import CustomerDirectory

    return CustomerDirectory()
