"""The read only tools this package may call, and which ones answer which topic.

ALLOWED_TOOLS is a hard allow list, separate from the access gate (plan 4.2,
ADR 4.4): a new tool on the hub is never callable from here until a person adds
it below. require_allowed() is run again right before every call.

Topic ids are the ids in backend/access/policy.json. Two product families are
known: "postpaid" (postpaid and fixed line, also the default when the product
is unknown) and "lte_prepaid".
"""

import datetime
from dataclasses import dataclass, field
from typing import Optional

POSTPAID = "postpaid"
LTE_PREPAID = "lte_prepaid"
PRODUCTS = (POSTPAID, LTE_PREPAID)

# Every tool that may ever be called. Nothing else, whatever the hub offers.
ALLOWED_TOOLS = frozenset(
    {
        "show_gb_balance",
        "show_lte_wallet_detail",
        "show_lte_purchased_packages",
        "show_bill_history",
        "show_last_payment",
        "show_reload_history",
        "show_service_detail",
        "show_lte_subscriber_status",
        "show_fault_list",
        "show_extra_gb_packages",
        "show_vas_data_bundle_packages",
    }
)

# Documentation of what is refused on purpose (the allow list is what counts):
# tools that change an account or send an SMS, the sign in tools, and two read
# only tools whose arguments cannot be filled with confidence yet.
REFUSED_TOOLS = frozenset(
    {
        "purchase_extra_gb",
        "add_vas_data_bundle",
        "log_fault",
        "subscribe_lte_prepaid_package",
        "authenticate_with_token",
        "refresh_customer_token",
        "validate_customer_and_send_otp",
        "verify_otp_and_authenticate",
        "show_daily_usage",
        "show_lte_prepaid_package_list",
    }
)


class ToolRefused(Exception):
    """A tool outside the read only allow list was about to be called."""

    def __init__(self, name: str):
        super().__init__(f"tool {name!r} is not on the read only allow list")
        self.tool = name


def require_allowed(name: str) -> None:
    if name not in ALLOWED_TOOLS:
        raise ToolRefused(str(name))


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict, compare=False, hash=False)
    topic: str = ""


def _reload_window() -> dict:
    today = datetime.date.today()
    return {"from_date": (today - datetime.timedelta(days=90)).isoformat(), "to_date": today.isoformat()}


# topic -> product -> list of (tool, argument factory). Arguments here never
# include telephone_no, the service adds the verified number.
_TABLE = {
    "balance": {
        POSTPAID: [("show_gb_balance", dict)],
        LTE_PREPAID: [("show_lte_wallet_detail", dict), ("show_lte_purchased_packages", dict)],
    },
    "bills_payments": {
        POSTPAID: [("show_bill_history", dict), ("show_last_payment", dict)],
        LTE_PREPAID: [("show_reload_history", _reload_window)],
    },
    "account": {
        POSTPAID: [("show_service_detail", dict)],
        LTE_PREPAID: [("show_service_detail", dict), ("show_lte_subscriber_status", dict)],
    },
    "faults": {
        POSTPAID: [("show_fault_list", dict)],
        LTE_PREPAID: [("show_fault_list", dict)],
    },
    "package_change": {
        POSTPAID: [
            ("show_extra_gb_packages", lambda: {"package_name": ""}),
            ("show_vas_data_bundle_packages", lambda: {"package_name": ""}),
        ],
        LTE_PREPAID: [("show_lte_purchased_packages", dict)],
    },
}
_TABLE["usage"] = _TABLE["balance"]
_TABLE["connection_status"] = _TABLE["account"]

TOPICS = tuple(_TABLE)


def calls_for(topics, product: Optional[str] = None) -> list:
    """The ToolCalls for the topics, in topic order, without duplicates.
    An unknown product is treated as postpaid."""
    family = product if product in PRODUCTS else POSTPAID
    seen: set = set()
    calls: list = []
    for topic in topics:
        for name, factory in _TABLE.get(topic, {}).get(family, []):
            if name in seen:
                continue
            require_allowed(name)
            seen.add(name)
            calls.append(ToolCall(name, factory(), topic))
    return calls
