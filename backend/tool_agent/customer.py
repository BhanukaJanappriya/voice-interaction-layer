"""Account answers for a verified customer.

The account topics (balance, usage, bills, last payment, service detail,
faults, packages) are fetched by customer_service, which calls every tool
through mcp_gateway on the customer's own pooled connection. This module adds
two optional tools the same way:

  show_daily_usage   only when the customer names a day of the month
  compare_package    needs the current package name and price, taken from the
                     tool results already fetched (show_service_detail or
                     show_gb_balance); when they cannot be read, the step is
                     skipped and the model is told what is missing

At most MAX_CALLS tool calls are made per turn, in parallel inside
config.CUSTOMER_TOOLS_BUDGET_SECONDS. Nothing here logs arguments, results or
customer text.
"""

import concurrent.futures as futures
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import config
import customer_service
import mcp_gateway
from customer_service import render

MAX_CALLS = 4

_pool = futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-agent")

_USAGE_WORDS = re.compile(
    r"(?i)usage|\bused\b|consum|\bdata\b|භාවිත|පරිභෝජන|පාවිච්චි|දත්ත|பயன்பாடு|பயன்படுத்த|டேட்டா"
)
_DAY = re.compile(
    r"(?i)(?<![\d/.:-])(\d{1,2})(?:st|nd|rd|th)\b"
    r"|\bday\s*(\d{1,2})\b"
    r"|\bon\s+(?:the\s+)?(\d{1,2})\b(?![\d/.:-])"
    r"|(?<!\d)(\d{1,2})\s*(?:වෙනි|වැනි|වන)\s*(?:දින|දා)?"
    r"|(?<!\d)(\d{1,2})\s*(?:ஆம் தேதி|ம் தேதி|ஆம்)"
)
_COMPARE = re.compile(
    r"(?i)compar|better (?:package|plan|one)|upgrade|cheaper|higher (?:package|plan)|what.?s better|"
    r"වඩා හොඳ|සසඳ|උසස් කර|ஒப்பிட|மேம்படுத்த|சிறந்த தொகுப்பு"
)
_NAME_KEYS = {"packagename", "package", "subscriberpackage", "currentpackage", "planname", "plan"}
_PRICE_KEYS = {
    "price", "packageprice", "monthlyprice", "monthlyfee", "monthlyrental", "rental", "currentprice",
    "packagerental", "monthlycharge", "packagefee",
}
_BLOCK = re.compile(r"(?m)^\[([a-z_]+)\]\n")


@dataclass
class CustomerResult:
    plan: Optional[Any] = None  # a TurnPlan, or None when the customer path does not apply
    missing: str = ""  # what could not be fetched, for the model clause
    timings: dict = field(default_factory=dict)  # tool -> ms


def day_number(text: str) -> Optional[int]:
    """The day of the month (1 to 31) the customer names when asking about usage."""
    if not isinstance(text, str) or not _USAGE_WORDS.search(text):
        return None
    for match in _DAY.finditer(text):
        for group in match.groups():
            if group and 1 <= int(group) <= 31:
                return int(group)
    return None


def wants_compare(text: str) -> bool:
    return isinstance(text, str) and bool(_COMPARE.search(text))


def split_blocks(context: str) -> list:
    """[(tool name, body)] from a customer_service context text."""
    if not context:
        return []
    parts = _BLOCK.split(context)
    return [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _price(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        found = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if found:
            number = float(found.group(0).replace(",", ""))
            return number if number > 0 else None
    return None


def package_facts(blocks: list) -> tuple:
    """(package name, monthly price) found in tool results, each None when
    it cannot be read."""
    name, price = None, None
    for _, body in blocks:
        try:
            data = json.loads(body)
        except ValueError:
            continue
        for key, value in _walk(data):
            flat = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if name is None and flat in _NAME_KEYS and isinstance(value, str) and value.strip():
                name = value.strip()[:120]
            if price is None and flat in _PRICE_KEYS:
                price = _price(value)
    return name, price


def _extra(tool: str, arguments: dict, access, budget: float):
    """(block or None, kind, ms) of one extra tool call."""
    result = mcp_gateway.call(tool, arguments, access, budget=budget)
    if result.kind != "ok":
        return None, result.kind, result.ms
    payload = render.extract(result.text)
    if render.payload_error(payload) is not None:
        return None, "error", result.ms
    return render.block(tool, result.text), "ok", result.ms


_STATUS_BY_TOPIC = {
    "balance": "tool_agent.status.balance",
    "usage": "tool_agent.status.balance",
    "bills_payments": "tool_agent.status.bill",
    "faults": "tool_agent.status.fault",
    "package_change": "tool_agent.status.packages",
    "account": "tool_agent.status.account",
    "connection_status": "tool_agent.status.account",
}


def run(text: str, language: Optional[str], access, deadline: float, on_status=None) -> CustomerResult:
    """Blocking, never raises. plan is None when this is not an account question.
    on_status(key) is called once, before the hub is asked, with the i18n key of
    a short status line such as "Checking your balance"."""
    from .messages import STATUS_BY_TOOL, customer_clause, text as translate
    from .plan import CANNED, GROUNDED, TurnPlan

    found = customer_service.detect(text, language, access)
    day = day_number(text)
    compare = wants_compare(text)
    if not found.topics and not day and not compare:
        return CustomerResult()
    if on_status is not None:
        key = "tool_agent.status.usage" if day and not found.topics else next(
            (_STATUS_BY_TOPIC[t] for t in found.topics if t in _STATUS_BY_TOPIC), "tool_agent.status.account"
        )
        try:
            on_status(key)
        except Exception:  # noqa: BLE001  a status line must never cost the answer
            pass

    def remaining() -> float:
        return max(deadline - time.monotonic(), 0.3)

    reserved = (1 if day else 0) + (2 if compare else 0)
    cap = max(1, MAX_CALLS - reserved)
    timings: dict = {}
    usage_future = _pool.submit(_extra, "show_daily_usage", {"bill_date": day}, access, remaining()) if day else None

    outcome = None
    if found.topics:
        outcome = customer_service.fetch(text, language, access, max_calls=cap)
        timings.update(outcome.timings_ms)
    elif not day and not compare:
        return CustomerResult()

    usage_block, usage_kind = None, ""
    if usage_future is not None:
        try:
            usage_block, usage_kind, ms = usage_future.result(timeout=remaining() + 0.5)
            timings["show_daily_usage"] = ms
        except Exception:  # noqa: BLE001
            usage_kind = "timeout"

    if (outcome is not None and outcome.kind == "reauth") or usage_kind == "reauth":
        return CustomerResult(
            TurnPlan(CANNED, canned=translate("tool_agent.session_expired", language), expired=True), "", timings
        )
    if outcome is not None and outcome.kind == "unavailable" and usage_block is None:
        return CustomerResult(TurnPlan(CANNED, canned=outcome.canned), "", timings)

    blocks = split_blocks(outcome.context) if outcome is not None and outcome.kind == "data" else []
    tools = list(outcome.tools) if outcome is not None and outcome.kind == "data" else []
    if usage_block is not None:
        blocks.append(usage_block)
        tools.append("show_daily_usage")

    missing = ""
    if compare:
        name, price = package_facts(blocks)
        if name is None or price is None:
            detail, kind, ms = _extra("show_service_detail", {}, access, remaining()) if not any(
                n == "show_service_detail" for n, _ in blocks
            ) else (None, "", 0)
            if kind == "reauth":
                return CustomerResult(
                    TurnPlan(CANNED, canned=translate("tool_agent.session_expired", language), expired=True), "", timings
                )
            if detail is not None:
                timings["show_service_detail"] = ms
                blocks.append(detail)
                tools.append("show_service_detail")
                name, price = package_facts(blocks)
        if name is None or price is None:
            lacking = [label for label, value in (("package name", name), ("monthly price", price)) if value is None]
            missing = "the customer's current " + " and ".join(lacking) + ", so packages could not be compared"
        else:
            block, kind, ms = _extra(
                "compare_package", {"current_package_name": name, "current_price": price}, access, remaining()
            )
            timings["compare_package"] = ms
            if block is not None:
                blocks.append(block)
                tools.append("compare_package")
            else:
                missing = "the package comparison, the comparison service did not answer"

    if not blocks:
        return CustomerResult(None, missing, timings)

    limit = int(getattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000))
    plan = TurnPlan(
        kind=GROUNDED,
        context=render.assemble(blocks, limit),
        clause=customer_clause(language, missing),
        private=not getattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", False),
        mode_suffix=" + SLT account data",
        status_key=STATUS_BY_TOOL.get(tools[0]) if tools else None,
        tools=tuple(dict.fromkeys(tools)),
    )
    return CustomerResult(plan, missing, timings)
