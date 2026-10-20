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
from datetime import datetime, timezone
from typing import Any, Optional

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_KEY = "mock-hub-key-0123456789"

# Fake customers of the customer_tools mode, national form like the hub's
# documentation. One product type per number.
FIBRE_NUMBER = "0112345678"  # postpaid fibre
LTE_NUMBER = "0712345670"  # LTE prepaid
NOT_AUTHENTICATED = "Customer not authenticated. Call validate_customer_and_send_otp first."
NO_ACTIVE_SESSION = "No active session for {number}. Ask the customer to authenticate with validate_customer_and_send_otp."
OTP_CHANNELS = {1: "sms", 2: "email", 3: "voice"}


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
        if scope["path"] in ("/api/otp/send", "/api/otp/verify") and hub.rest_otp and scope["method"] == "POST":
            await hub._rest(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _reply(send: Send, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
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
        # Numbers that any connection may use (a shortcut for tests that do not
        # care about login). The real hub ties a login to ONE connection, and so
        # does this mock: verify adds the number to connection_auth only.
        self.authenticated: set[str] = set()
        self.connection_auth: dict[int, tuple[Any, set[str]]] = {}  # id(server session) -> (session, numbers)
        self.latencies: dict[str, Any] = {"search_knowledgebase_tool": {"calls": 3, "avg_ms": 1200.0}}
        self.customer_products: dict[str, str] = {FIBRE_NUMBER: "postpaid", LTE_NUMBER: "lte_prepaid"}
        self.customer_tool_errors: dict[str, str] = {}  # tool name -> error text to raise
        self.customer_tool_delays: dict[str, float] = {}  # tool name -> seconds to wait
        self.customer_names: dict[str, str] = {}  # national number -> the name show_service_detail reports
        self.customer_payloads: dict[str, str] = {}  # tool name -> raw reply text instead of the fake JSON
        # The hub's REST sign in (/api/otp/send and /api/otp/verify), off by default: with rest_otp False these
        # paths answer 404 like a hub without them. The success shapes are a GUESS (not seen on the real hub).
        self.rest_otp = False
        self.rest_status: Optional[int] = None  # force this HTTP status (404, 500, ...) with a plain text body
        self.rest_not_json = False  # answer 200 with a non JSON body
        self.rest_verify_status: Optional[int] = None  # like rest_status, for /api/otp/verify only
        # flat | camel | pascal | data | result | tokens | token | missing | no_account
        self.rest_token_shape = "flat"
        # unix | ms | iso | in (expires_in seconds)
        self.rest_expiry_style = "unix"
        self.rest_token_ttl = 3600.0  # seconds from now; negative: already expired when issued
        self.issued_tokens: dict[str, dict] = {}  # national number -> the tokens last issued
        self.rest_calls: list[tuple[str, str]] = []  # ("send" | "verify", national number)
        self.seed_fail = False  # authenticate_with_token answers success false whatever the tokens
        self.seed_text_error = False  # ... or an "Error ..." text instead of JSON
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
                if hub.customer_tools:
                    return json.dumps(
                        {"query": query, "chunks": [{"content": hub.search_text, "score": 0.91}], "count": 1}
                    )
                return hub.search_text

        if allowed("purchase_extra_gb") and not hub.customer_tools:

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
            async def validate_customer_and_send_otp(telephone_no: str, ctx: Context, otp_media: int = 2) -> str:
                if hub.customer_tools:
                    return hub._real_validate(record, telephone_no, otp_media)
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
            async def verify_otp_and_authenticate(telephone_no: str, session_id: str, otp_value: str, ctx: Context) -> str:
                if hub.customer_tools:
                    return hub._real_verify(record, ctx, telephone_no, session_id, otp_value)
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

    # REST sign in

    async def _rest(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        path = scope["path"]
        forced = self.rest_status
        if path.endswith("/verify") and self.rest_verify_status is not None:
            forced = self.rest_verify_status
        if forced is not None:
            await _reply(send, forced, b"forced failure")
            return
        if self.rest_not_json:
            await _reply(send, 200, b"<html>not json</html>")
            return
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {}
        status, answer = self._rest_answer(path, data)
        await _reply(send, status, json.dumps(answer).encode("utf-8"), "application/json")

    def _rest_answer(self, path: str, data: dict) -> "tuple[int, dict]":
        phone = data.get("telephone_no")
        if path.endswith("/send"):
            if not phone:
                return 400, {"success": False, "error": "telephone_no is required"}
            with self._lock:
                self.rest_calls.append(("send", self._national(phone)))
            return 200, self._rest_send(str(phone), data.get("otp_media", 2))
        session_id, code = data.get("session_id"), data.get("otp_value")
        if not (phone and session_id and code):
            return 400, {"success": False, "error": "telephone_no, session_id, and otp_value are required"}
        with self._lock:
            self.rest_calls.append(("verify", self._national(phone)))
            session = self._otp_sessions.get(session_id)
        if session is None or session["phone"] != phone or str(code) != self.otp_code:
            return 200, {"success": False, "error": "OTP verification failed: invalid or expired code"}
        with self._lock:
            self._otp_sessions.pop(session_id, None)  # single use
        return 200, self._issue_tokens(self._national(phone))

    def _rest_send(self, telephone_no: str, otp_media: Any) -> dict:
        channel = OTP_CHANNELS.get(otp_media, "email")
        session_id = uuid.uuid4().hex[:16]
        customer = self.otp_customers.get(telephone_no)
        if customer is None:
            return {"success": False,
                    "error": "Customer validation failed: {'status': 'error', 'message': 'Customer not found'}",
                    "session_id": session_id}
        with self._lock:
            self._otp_sessions[session_id] = {"phone": telephone_no, "tries": 0}
            self.sms_outbox.append({"to": telephone_no, "code": self.otp_code, "session_id": session_id, "channel": channel})
        contact = (
            "{'mobile' : '%s','email' : '%s','accountCategory' : 'Individual'}"
            % (customer.get("masked_mobile", "XXXXXX4567"), customer.get("masked_email", "XXXXXXXXXXth22@gmail.com"))
        )
        return {"success": True, "session_id": session_id, "contact_info": contact, "otp_status": "OTP send success"}

    def _issue_tokens(self, number: str) -> dict:
        expires = time.time() + self.rest_token_ttl
        record = {
            "access_token": "acc-" + uuid.uuid4().hex,
            "refresh_token": "ref-" + uuid.uuid4().hex,
            "subscriber_id": "SUB-0001",
            "account_number": "ACC-0001",
            "expires_at": expires,
        }
        with self._lock:
            self.issued_tokens[number] = dict(record)
        style = self.rest_expiry_style
        if style == "ms":
            expiry = {"expires_at": int(expires * 1000)}
        elif style == "iso":
            expiry = {"expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}
        elif style == "in":
            expiry = {"expires_in": int(self.rest_token_ttl)}
        else:
            expiry = {"expires_at": int(expires)}
        camel_expiry = {("expiresAt" if k == "expires_at" else "expiresIn"): v for k, v in expiry.items()}
        fields = {k: record[k] for k in ("access_token", "refresh_token", "subscriber_id", "account_number")}
        shape = self.rest_token_shape
        if shape == "missing":
            return {"success": True, "message": "Authenticated"}
        if shape == "no_account":
            fields.pop("account_number")
        if shape == "camel":
            return {"success": True, "accessToken": fields["access_token"], "refreshToken": fields["refresh_token"],
                    "subscriberId": fields["subscriber_id"], "accountNumber": fields["account_number"], **camel_expiry}
        if shape == "pascal":
            return {"Success": True, "AccessToken": fields["access_token"], "RefreshToken": fields["refresh_token"],
                    "SubscriberId": fields["subscriber_id"], "AccountNumber": fields["account_number"],
                    **{k[0].upper() + k[1:]: v for k, v in camel_expiry.items()}}
        if shape in ("data", "result"):
            return {"success": True, shape: {**fields, **expiry}}
        if shape == "tokens":  # tokens in their own object, the ids beside it
            return {"success": True, "subscriber_id": fields["subscriber_id"], "account_number": fields["account_number"],
                    "tokens": {"access_token": fields["access_token"], "refresh_token": fields["refresh_token"], **expiry}}
        if shape == "token":
            return {"success": True, "subscriber_id": fields["subscriber_id"], "account_number": fields["account_number"],
                    "token": {"accessToken": fields["access_token"], "refreshToken": fields["refresh_token"], **camel_expiry}}
        return {"success": True, **fields, **expiry}

    # Customer tools

    @staticmethod
    def _national(value: str) -> str:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if digits.startswith("94") and len(digits) == 11:
            digits = "0" + digits[2:]
        return digits

    # The real hub's login belongs to one MCP connection (measured live): the
    # verify call on connection A authenticates tools on A only.

    def _connection_numbers(self, ctx: Context) -> set:
        # This SDK builds a new ServerSession wrapper per request; the Connection
        # object under it is the one that lives as long as the MCP connection.
        session = getattr(ctx.session, "_connection", None) or ctx.session
        with self._lock:
            entry = self.connection_auth.get(id(session))
            if entry is None or entry[0] is not session:
                entry = (session, set())  # the session is kept so its id is never reused
                self.connection_auth[id(session)] = entry
            return entry[1]

    def _real_validate(self, record, telephone_no: str, otp_media: int) -> str:
        channel = OTP_CHANNELS.get(otp_media, "email")
        record("validate_customer_and_send_otp", telephone_no=telephone_no, otp_media=otp_media, channel=channel)
        session_id = uuid.uuid4().hex[:16]
        customer = self.otp_customers.get(telephone_no)
        if customer is None:
            return json.dumps({
                "success": False,
                "error": "Customer validation failed: {'status': 'error', 'message': 'Customer not found'}",
                "session_id": session_id})
        with self._lock:
            self._otp_sessions[session_id] = {"phone": telephone_no, "tries": 0}
            self.sms_outbox.append(
                {"to": telephone_no, "code": self.otp_code, "session_id": session_id, "channel": channel})
        contact = (
            "{'mobile' : '%s','email' : '%s','accountCategory' : 'Individual'}"
            % (customer.get("masked_mobile", "XXXXXX4567"), customer.get("masked_email", "XXXXXXXXXXth22@gmail.com"))
        )
        return json.dumps({"success": True, "session_id": session_id, "contact_info": contact,
                           "otp_status": "OTP send success"})

    def _real_verify(self, record, ctx: Context, telephone_no: str, session_id: str, otp_value: str) -> str:
        record("verify_otp_and_authenticate", telephone_no=telephone_no, session_id=session_id, otp_value=otp_value)
        with self._lock:
            session = self._otp_sessions.get(session_id)
        if session is None or session["phone"] != telephone_no or otp_value != self.otp_code:
            return json.dumps({"success": False, "error": "OTP verification failed: invalid or expired code"})
        customer = self.otp_customers.get(telephone_no, {})
        self._connection_numbers(ctx).add(self._national(telephone_no))
        return json.dumps({
            "success": True, "subscriber_id": "SUB-0001", "account_number": "ACC-0001",
            "subscriber_package": customer.get("package", "Fibre Unlimited 60"),
            "privileges": "view balance, view bills", "note": "Authenticated; use the other tools with telephone_no."})

    def _seed(self, ctx: Context, telephone_no: str, access: str, refresh: str, subscriber: str, account: str, expires: float) -> str:
        """authenticate_with_token: marks THIS connection authenticated when the tokens are the ones the REST
        verify issued for the number (and not yet expired), like verify does on its own connection."""
        number = self._national(telephone_no)
        if self.seed_text_error:
            return "Error: authentication failed"
        if self.seed_fail:
            return json.dumps({"success": False, "error": "Token authentication failed"})
        with self._lock:
            issued = self.issued_tokens.get(number)
        if issued is None or access != issued["access_token"] or refresh != issued["refresh_token"] or \
                subscriber != issued["subscriber_id"] or account != issued["account_number"]:
            return json.dumps({"success": False, "error": "Invalid access token"})
        if float(expires) <= time.time() or issued["expires_at"] <= time.time():
            return json.dumps({"success": False, "error": "Access token expired"})
        self._connection_numbers(ctx).add(number)
        return json.dumps({"success": True, "message": "Session authenticated from tokens", "telephone_no": number})

    def _customer_reply(self, name: str, telephone_no: str, product: Optional[str], build, ctx: Context) -> str:
        """Common path of every fake customer tool: authentication, forced
        errors, product type, then the JSON text. product is 'postpaid',
        'lte_prepaid' or None for tools that answer for both kinds."""
        number = self._national(telephone_no)
        with self._lock:
            known = number in self.authenticated
            forced = self.customer_tool_errors.get(name)
            raw = self.customer_payloads.get(name)
        if not known:
            known = number in self._connection_numbers(ctx)
        if not known:
            return json.dumps({"success": False, "error": NO_ACTIVE_SESSION.format(number=number)})
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

            async def tool(telephone_no: str, ctx: Context) -> str:
                record(name, telephone_no=telephone_no)
                delay = hub.customer_tool_delays.get(name)
                if delay:
                    await asyncio.sleep(delay)
                return hub._customer_reply(name, telephone_no, product, build, ctx)

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
             "package": "Fibre Unlimited 60", "speed": "100 Mbps",
             **({"customerName": hub.customer_names[n]} if n in hub.customer_names else {})}), "Service details.")
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
                    async def tool(telephone_no: str, ctx: Context, package_name: str = "") -> str:
                        record(tool_name, telephone_no=telephone_no, package_name=package_name)
                        return hub._customer_reply(tool_name, telephone_no, pay, packages, ctx)

                    return tool

                mcp.tool(name=name, description=description)(make(name))

        if allowed("show_reload_history"):

            @mcp.tool(name="show_reload_history", description="Reload history.")
            async def show_reload_history(telephone_no: str, ctx: Context, from_date: str = "", to_date: str = "") -> str:
                record("show_reload_history", telephone_no=telephone_no, from_date=from_date, to_date=to_date)
                return hub._customer_reply("show_reload_history", telephone_no, lte, lambda n, p: {
                    "status": "success", "reloads": [{"date": "2026-10-01", "amount": 500.0, "channel": "App"}]}, ctx)

        # Tools that change things: registered so a test can prove they are never called.
        changing = (
            ("purchase_extra_gb", {"success": True, "message": "Extra GB purchase submitted"}),
            ("add_vas_data_bundle", {"success": True, "message": "VAS bundle added"}),
            ("subscribe_lte_prepaid_package", {"success": True, "message": "Package subscribed"}),
        )
        for name, reply in changing:
            if allowed(name):

                def make_change(tool_name: str, body: dict):
                    async def tool(telephone_no: str, ctx: Context, package_id: int = 0) -> str:
                        record(tool_name, telephone_no=telephone_no, package_id=package_id)
                        return hub._customer_reply(tool_name, telephone_no, None, lambda n, p: body, ctx)

                    return tool

                mcp.tool(name=name, description="Changes the account (must never be called by the assistant).")(
                    make_change(name, reply)
                )
        if allowed("log_fault"):

            @mcp.tool(name="log_fault", description="Opens a fault ticket (must never be called by the assistant).")
            async def log_fault(telephone_no: str, ctx: Context, service_id: str = "") -> str:
                record("log_fault", telephone_no=telephone_no, service_id=service_id)
                return hub._customer_reply(
                    "log_fault", telephone_no, None, lambda n, p: {"success": True, "faultId": "F-2002"}, ctx)

        if allowed("compare_package"):

            @mcp.tool(name="compare_package", description="Compares two packages.")
            async def compare_package(current_package_name: str, current_price: float, top_k: int = 3) -> str:
                record("compare_package", current_package_name=current_package_name, current_price=current_price, top_k=top_k)
                return json.dumps({"success": True, "current": current_package_name, "alternatives": [{"name": "Unlimited Home", "price": 5900}]})

        if allowed("get_function_latencies"):

            @mcp.tool(name="get_function_latencies", description="Latency statistics of the hub tools.")
            async def get_function_latencies() -> str:
                record("get_function_latencies")
                return json.dumps(hub.latencies)

        if allowed("authenticate_with_token"):

            @mcp.tool(name="authenticate_with_token", description="Seeds a session (must never be called by the assistant).")
            async def authenticate_with_token(
                telephone_no: str, ctx: Context, access_token: str = "", refresh_token: str = "",
                subscriber_id: str = "", account_number: str = "", expires_at: float = 0.0,
            ) -> str:
                # The tokens are never recorded: only the number and that a seed was asked for.
                record("authenticate_with_token", telephone_no=telephone_no)
                return hub._seed(ctx, telephone_no, access_token, refresh_token, subscriber_id, account_number, expires_at)

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
