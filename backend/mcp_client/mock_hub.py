"""A local stand in for the SLT MCP hub, for tests and offline demos.

The real hub (https://mcphub.sltdigitallab.lk) answers 401 without a key
and no key exists on the development machine, so the client was built and
verified against this mock. It speaks real MCP through the SDK's SERVER
side, over either transport the client has to detect:

    MockHub(mode="sse")                       legacy SSE: GET /sse, POST /messages/
                                              (the default, like the real hub)
    MockHub(mode="streamable", path="/mcp")   streamable HTTP at /mcp
    MockHub(mode="streamable", path="/")      streamable HTTP at the root

and has the failure switches the client's tests need: a required API key
(401 otherwise), a number of upcoming requests answered 503, a delay on
the "slow_search" tool, and counters to assert on (how many requests came
in, how many were refused, which tools were called with what).

What the mock proves and what it cannot: it proves the client's transport
detection, header handling, timeout, retry, breaker and result parsing
against a conforming MCP server. It cannot show what the real hub's tool
names, schemas, result shapes or quirks are. See docs in the final report
and KNOWN_LIMITATIONS: the real hub is not live tested.

Importable by other test files:
    hub = MockHub(mode="sse"); url = hub.start(); ...; hub.stop()
"""

import asyncio
import json
import socket
import threading
import time
import uuid
from typing import Any, Optional

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_KEY = "mock-hub-key-0123456789"

# Fake customers of the customer_tools mode, national form like the hub's
# documentation. One product type per number.
FIBRE_NUMBER = "0112345678"  # postpaid fibre
LTE_NUMBER = "0712345670"  # LTE prepaid
NOT_AUTHENTICATED = "Customer not authenticated. Call validate_customer_and_send_otp first."


class _Gate:
    """Pure ASGI wrapper in front of the SDK app: counts requests, can
    answer 503 for a while, and refuses calls without the key. Lifespan
    events pass straight through (the SDK's session manager needs them)."""

    def __init__(self, app: ASGIApp, hub: "MockHub"):
        self.app = app
        self.hub = hub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        hub = self.hub
        with hub._lock:
            hub.requests += 1
            hub.request_log.append((scope["method"], scope["path"]))
            hub.request_key_ok.append(
                not hub.api_key
                or dict(scope["headers"]).get(hub.auth_header.lower().encode(), b"") == hub.api_key.encode()
            )
            failing = hub.fail_next > 0
            if failing:
                hub.fail_next -= 1
        if failing:
            await _reply(send, 503, b"Service Unavailable")
            return
        if hub.api_key:
            wanted = hub.api_key.encode()
            sent = dict(scope["headers"]).get(hub.auth_header.lower().encode(), b"")
            if sent != wanted:
                with hub._lock:
                    hub.auth_failures += 1
                await _reply(send, 401, b"Unauthorized")
                return
        await self.app(scope, receive, send)


async def _reply(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MockHub:
    def __init__(
        self,
        mode: str = "sse",
        path: str = "/mcp",
        api_key: str = DEFAULT_KEY,
        auth_header: str = "X-API-Key",
        json_response: bool = False,
        tools: Optional[list[str]] = None,
        customer_tools: bool = False,
    ):
        if mode not in ("streamable", "sse"):
            raise ValueError("mode must be 'streamable' or 'sse'")
        self.mode = mode
        self.path = path
        self.api_key = api_key
        self.auth_header = auth_header
        self.json_response = json_response
        self.enabled_tools = set(tools) if tools else None
        # Failure and behaviour switches, safe to change while running.
        self.fail_next = 0
        self.slow_seconds = 3.0
        self.customer: Optional[dict] = {
            "display_name": "Nimal Perera",
            "mobile": "0771234567",
            "email": "nimal@example.lk",
            "internal_account_no": "ACC-0001",  # must never be passed on by the lookup
        }
        self.customer_as_json_text = True
        self.search_text = "The MOBITEL 1598 package costs Rs. 1598 per month."
        # One time code tools (the real hub's validate_customer_and_send_otp
        # and verify_otp_and_authenticate). The mock "sends the SMS" by
        # appending to sms_outbox, so tests can read the code; the real
        # response shapes are not known yet and these are a guess.
        self.otp_customers: dict[str, dict] = {"0112345678": {"name": "Nimal Perera", "masked": "07X XXX X567"}}
        self.otp_masked_in_response = True
        self.otp_session_key = "session_id"
        self.otp_code = "123456"
        self.sms_outbox: list[dict] = []
        self._otp_sessions: dict[str, dict] = {}
        # Customer tools (read only plus decoys for the changing ones), off by
        # default so older tests see the older tool set. A customer tool for a
        # number outside `authenticated` is an error result, like the real hub
        # says it behaves; the OTP tools above add a number when verify succeeds.
        # The response shapes are a guess (unverified against the real hub).
        self.customer_tools = customer_tools
        self.authenticated: set[str] = set()
        self.customer_products: dict[str, str] = {FIBRE_NUMBER: "postpaid", LTE_NUMBER: "lte_prepaid"}
        self.customer_tool_errors: dict[str, str] = {}  # tool name -> error text to raise
        self.customer_tool_delays: dict[str, float] = {}  # tool name -> seconds to wait
        self.customer_payloads: dict[str, str] = {}  # tool name -> raw reply text instead of the fake JSON
        # Counters.
        self.requests = 0
        self.auth_failures = 0
        self.request_log: list[tuple[str, str]] = []
        self.request_key_ok: list[bool] = []  # per request: did it carry the right key header
        self.tool_calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0

    # Tools

    def _build_mcp(self) -> MCPServer:
        mcp = MCPServer("mock-hub")
        hub = self

        def allowed(name: str) -> bool:
            return hub.enabled_tools is None or name in hub.enabled_tools

        def record(name: str, **arguments: Any) -> None:
            with hub._lock:
                hub.tool_calls.append((name, arguments))

        if allowed("search_knowledgebase_tool"):

            @mcp.tool(name="search_knowledgebase_tool", description="Search the SLT knowledge base.")
            async def search_knowledgebase_tool(query: str, top_k: int = 3) -> str:
                record("search_knowledgebase_tool", query=query, top_k=top_k)
                return hub.search_text

        if allowed("purchase_extra_gb"):

            @mcp.tool(name="purchase_extra_gb", description="Buys extra GB (must never be called by the assistant).")
            async def purchase_extra_gb(telephone_no: str) -> str:
                record("purchase_extra_gb", telephone_no=telephone_no)
                return "purchased"

        if allowed("show_gb_balance") and not hub.customer_tools:

            @mcp.tool(name="show_gb_balance", description="Customer data balance.")
            async def show_gb_balance(telephone_no: str) -> str:
                record("show_gb_balance", telephone_no=telephone_no)
                return "10 GB"

        if allowed("validate_customer_and_send_otp"):

            @mcp.tool(name="validate_customer_and_send_otp", description="Checks the customer and sends an OTP.")
            async def validate_customer_and_send_otp(telephone_no: str, otp_media: int = 2) -> str:
                record("validate_customer_and_send_otp", telephone_no=telephone_no, otp_media=otp_media)
                customer = hub.otp_customers.get(telephone_no)
                if customer is None:
                    return json.dumps({"status": "error", "message": "customer not found"})
                session_id = uuid.uuid4().hex[:12]
                hub._otp_sessions[session_id] = {"phone": telephone_no, "tries": 0}
                hub.sms_outbox.append({"to": telephone_no, "code": hub.otp_code, "session_id": session_id})
                body = {"status": "success", hub.otp_session_key: session_id, "message": "OTP sent"}
                if hub.otp_masked_in_response:
                    body["masked_destination"] = customer["masked"]
                return json.dumps(body)

        if allowed("verify_otp_and_authenticate"):

            @mcp.tool(name="verify_otp_and_authenticate", description="Verifies the OTP.")
            async def verify_otp_and_authenticate(telephone_no: str, session_id: str, otp_value: str) -> str:
                record("verify_otp_and_authenticate", telephone_no=telephone_no, session_id=session_id, otp_value=otp_value)
                session = hub._otp_sessions.get(session_id)
                if session is None or session["phone"] != telephone_no or otp_value != hub.otp_code:
                    return json.dumps({"status": "error", "authenticated": False})
                customer = hub.otp_customers.get(telephone_no, {})
                with hub._lock:
                    hub.authenticated.add(telephone_no)
                return json.dumps(
                    {"status": "success", "authenticated": True, "access_token": "hub-token-not-used",
                     "customer_name": customer.get("name")}
                )

        if allowed("search_hub"):

            @mcp.tool(name="search_hub", description="Search the hub knowledge for a question.")
            async def search_hub(query: str) -> str:
                record("search_hub", query=query)
                return hub.search_text

        if allowed("plan_notes"):

            @mcp.tool(name="plan_notes", description="Notes about a plan or topic.")
            async def plan_notes(topic: str) -> str:
                record("plan_notes", topic=topic)
                return "Plan notes: " + ("x" * 1200)  # longer than the per tool cap on purpose

        if allowed("slow_search"):

            @mcp.tool(name="slow_search", description="Search that takes a while.")
            async def slow_search(query: str) -> str:
                record("slow_search", query=query)
                await asyncio.sleep(hub.slow_seconds)
                return "slow answer"

        if allowed("two_args"):

            @mcp.tool(name="two_args", description="Needs two arguments, so never eligible automatically.")
            async def two_args(a: str, b: str) -> str:
                record("two_args", a=a, b=b)
                return "two"

        if allowed("count_items"):

            @mcp.tool(name="count_items", description="Takes a number, not text.")
            async def count_items(n: int) -> str:
                record("count_items", n=n)
                return "counted"

        if allowed("failing_tool"):

            @mcp.tool(name="failing_tool", description="Always reports an error.")
            async def failing_tool(query: str) -> str:
                record("failing_tool", query=query)
                raise ValueError("the tool refuses this request")

        if allowed("find_customer"):

            @mcp.tool(name="find_customer", description="Find a customer by landline number.")
            async def find_customer(phone: str) -> str:
                record("find_customer", phone=phone)
                if hub.customer is None:
                    return json.dumps({"found": False}) if hub.customer_as_json_text else "No such customer."
                if hub.customer_as_json_text:
                    return json.dumps({"found": True, "customer": hub.customer})
                return "Customer: " + hub.customer["display_name"]

        if hub.customer_tools:
            hub._register_customer_tools(mcp, allowed, record)

        return mcp

    # Customer tools

    @staticmethod
    def _national(value: str) -> str:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if digits.startswith("94") and len(digits) == 11:
            digits = "0" + digits[2:]
        return digits

    def _customer_reply(self, name: str, telephone_no: str, product: Optional[str], build) -> str:
        """Common path of every fake customer tool: authentication, forced
        errors, product type, then the JSON text. product is 'postpaid',
        'lte_prepaid' or None for tools that answer for both kinds."""
        number = self._national(telephone_no)
        with self._lock:
            known = number in self.authenticated
            forced = self.customer_tool_errors.get(name)
            raw = self.customer_payloads.get(name)
        if not known:
            raise ToolError(NOT_AUTHENTICATED)
        if forced:
            raise ToolError(forced)
        actual = self.customer_products.get(number, "postpaid")
        if product is not None and actual != product:
            kind = "an LTE prepaid" if product == "lte_prepaid" else "a postpaid or fixed"
            raise ToolError(f"Not applicable: this service is not {kind} connection")
        return raw if raw is not None else json.dumps(build(number, actual))

    def _register_customer_tools(self, mcp: MCPServer, allowed, record) -> None:
        hub = self
        pay, lte = "postpaid", "lte_prepaid"

        def simple(name: str, product: Optional[str], build, description: str) -> None:
            if not allowed(name):
                return

            async def tool(telephone_no: str) -> str:
                record(name, telephone_no=telephone_no)
                delay = hub.customer_tool_delays.get(name)
                if delay:
                    await asyncio.sleep(delay)
                return hub._customer_reply(name, telephone_no, product, build)

            mcp.tool(name=name, description=description)(tool)

        simple("show_gb_balance", pay, lambda n, p: {
            "status": "success", "remainingGB": 42.5, "usedGB": 17.5, "totalGB": 60,
            "packageName": "Fibre Unlimited 60", "validTill": "2026-10-31"}, "Data balance and usage summary.")
        simple("show_bill_history", pay, lambda n, p: {
            "status": "success", "billAmount": 4250.75, "dueDate": "2026-10-25",
            "history": [{"month": "2026-09", "billAmount": 4100.0}, {"month": "2026-08", "billAmount": 3980.5},
                        {"month": "2026-07", "billAmount": 4020.0}]}, "Last bill and history.")
        simple("show_last_payment", pay, lambda n, p: {
            "status": "success", "paymentAmount": 4100.0, "paymentDate": "2026-09-27", "channel": "Online"},
            "Last payment.")
        simple("show_service_detail", None, lambda n, p: (
            {"status": "success", "serviceType": "LTE", "accountType": "Prepaid", "serviceStatus": "Active",
             "package": "LTE Prepaid Super"} if p == lte else
            {"status": "success", "serviceType": "Fibre", "accountType": "Postpaid", "serviceStatus": "Active",
             "package": "Fibre Unlimited 60", "speed": "100 Mbps"}), "Service details.")
        simple("show_fault_list", None, lambda n, p: {"status": "success", "faults": [
            {"faultId": "F-1001", "status": "Closed", "nature": "No internet", "loggedOn": "2026-09-02"}]}, "Faults.")
        simple("show_lte_subscriber_status", lte, lambda n, p: {
            "status": "success", "subscriberStatus": "Active", "activatedOn": "2025-03-14"}, "LTE subscriber status.")
        simple("show_lte_wallet_detail", lte, lambda n, p: {
            "status": "success", "walletBalance": 350.0, "currency": "LKR", "expiresOn": "2026-11-20"}, "LTE wallet.")
        simple("show_lte_purchased_packages", lte, lambda n, p: {"status": "success", "packages": [
            {"name": "Daily 5GB", "remainingGB": 2.1, "usedGB": 2.9, "expiresOn": "2026-10-14"}]}, "LTE packages.")
        simple("show_lte_prepaid_package_list", lte, lambda n, p: {"status": "success", "packages": []},
               "LTE package list (never used by the assistant).")
        simple("show_daily_usage", pay, lambda n, p: {"status": "success", "days": []},
               "Daily usage (never used by the assistant).")
        simple("refresh_customer_token", None, lambda n, p: {"status": "success"},
               "Refresh token (never used by the assistant).")

        def packages(n, p):
            return {"status": "success", "packages": [
                {"packageId": 11, "name": "Extra 10GB", "price": 490.0},
                {"packageId": 12, "name": "Extra 25GB", "price": 990.0}]}

        for name, description in (
            ("show_extra_gb_packages", "Extra GB packages."),
            ("show_vas_data_bundle_packages", "VAS data bundles."),
        ):
            if allowed(name):

                def make(tool_name: str):
                    async def tool(telephone_no: str, package_name: str = "") -> str:
                        record(tool_name, telephone_no=telephone_no, package_name=package_name)
                        return hub._customer_reply(tool_name, telephone_no, pay, packages)

                    return tool

                mcp.tool(name=name, description=description)(make(name))

        if allowed("show_reload_history"):

            @mcp.tool(name="show_reload_history", description="Reload history.")
            async def show_reload_history(telephone_no: str, from_date: str = "", to_date: str = "") -> str:
                record("show_reload_history", telephone_no=telephone_no, from_date=from_date, to_date=to_date)
                return hub._customer_reply("show_reload_history", telephone_no, lte, lambda n, p: {
                    "status": "success", "reloads": [{"date": "2026-10-01", "amount": 500.0, "channel": "App"}]})

        # Tools that change things: registered so a test can prove they are never called.
        for name in ("add_vas_data_bundle", "subscribe_lte_prepaid_package"):
            if allowed(name):

                def make_change(tool_name: str):
                    async def tool(telephone_no: str, package_id: int = 0) -> str:
                        record(tool_name, telephone_no=telephone_no, package_id=package_id)
                        return "changed"

                    return tool

                mcp.tool(name=name, description="Changes the account (must never be called by the assistant).")(
                    make_change(name)
                )
        if allowed("log_fault"):

            @mcp.tool(name="log_fault", description="Opens a fault ticket (must never be called by the assistant).")
            async def log_fault(telephone_no: str, service_id: str = "") -> str:
                record("log_fault", telephone_no=telephone_no, service_id=service_id)
                return "logged"

        if allowed("authenticate_with_token"):

            @mcp.tool(name="authenticate_with_token", description="Seeds a session (must never be called by the assistant).")
            async def authenticate_with_token(telephone_no: str, access_token: str = "") -> str:
                record("authenticate_with_token", telephone_no=telephone_no)
                return "ok"

    # Lifecycle

    def start(self) -> str:
        mcp = self._build_mcp()
        if self.mode == "streamable":
            inner = mcp.streamable_http_app(
                streamable_http_path=self.path, json_response=self.json_response, stateless_http=False
            )
        else:
            inner = mcp.sse_app(sse_path="/sse", message_path="/messages/")
        app = _Gate(inner, self)
        self.port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error", lifespan="on")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._thread = threading.Thread(target=lambda: asyncio.run(self._server.serve()), daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("mock hub did not start")
        return self.url

    @property
    def url(self) -> str:
        """The base URL to give the client as MCP_HUB_URL. For the SSE
        mode and for a streamable hub at /mcp this is just the origin,
        which the client has to resolve by itself."""
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._server = None
        self._thread = None

    def reset_counters(self) -> None:
        with self._lock:
            self.requests = 0
            self.auth_failures = 0
            self.request_log.clear()
            self.request_key_ok.clear()
            self.tool_calls.clear()


def run_forever(mode: str = "streamable") -> None:  # pragma: no cover
    """`python -m mcp_client.mock_hub sse` for manual poking."""
    hub = MockHub(mode=mode)
    print(f"Mock hub ({mode}) at {hub.start()}  header {hub.auth_header}: {hub.api_key}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        hub.stop()


if __name__ == "__main__":  # pragma: no cover
    import sys

    run_forever(sys.argv[1] if len(sys.argv) > 1 else "streamable")
