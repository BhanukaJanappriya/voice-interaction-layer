# SLT MCP hub: connection, tools and the customer lookup gap

Verified on 2026-10-12 and 2026-10-13 with `python -m backend.mcp_client.check`
(list tools only; no customer tool and no mutating tool was ever called).

## 1. How the application connects

| Item | Value |
|---|---|
| Hub | https://mcphub.sltdigitallab.lk (nginx in front, Ubuntu) |
| Transport | Legacy MCP SSE: `GET /sse` returns 200 `text/event-stream`, first event `endpoint`; the client then POSTs JSON-RPC messages to the endpoint the hub names |
| Streamable HTTP | Not offered. `POST /` and `POST /mcp` give 404, `POST /sse` gives 405 (allow: GET, HEAD) |
| Authentication | Header `X-API-Key: <key>` on every request. Without it every path gives 401 |
| Client | `backend/mcp_client/client.py` (`HubClient`), official MCP SDK, one short lived session per operation |

Settings (`backend/.env`, read by `backend/config.py`):

| Name | Default | Meaning |
|---|---|---|
| `MCP_HUB_URL` | `https://mcphub.sltdigitallab.lk` | Base URL. A trailing `/sse` or `/mcp` is accepted |
| `MCP_HUB_AUTH_HEADER` | `X-API-Key` | Header that carries the key. For `Authorization` a key without a space gets `Bearer ` in front |
| `MCP_HUB_API_KEY` | empty | The key. Empty means the hub is treated as not configured. Never logged, never in `/health`, scrubbed from error text |
| `MCP_HUB_ENABLED` | `true` | Master switch |
| `MCP_HUB_TIMEOUT_SECONDS` | `8` | Per attempt timeout |
| `MCP_HUB_RETRIES` | `2` | Retries for network class failures (not for 401/403) |
| `MCP_HUB_BREAKER_SECONDS` | `300` | After a failed call the hub is skipped for this long |
| `MCP_HUB_IN_REPLIES` | `false` | Whether the knowledge search feeds spoken replies (3 to 7 s each, so off) |
| `MCP_HUB_TOOLS` | empty | Allow list of tools offered to the model as context |
| `MCP_HUB_CUSTOMER_LOOKUP_TOOL` | empty | Name of a hub tool that looks a customer up (none exists today) |

Behaviour of the client, in the order a call meets it:

1. Breaker: while open, calls raise `HubBreakerOpen` at once, with no traffic.
2. Retry: connection, DNS, TLS, timeout and HTTP 408/425/429/5xx failures are retried
   `MCP_HUB_RETRIES` times with exponential backoff (0.25 s base, 2 s cap) and
   equal jitter (each wait is between half and the full backoff), all inside one
   time budget. 401/403 are never retried.
3. Transport detection, once, then cached: SSE at `/sse` first, then streamable
   HTTP at the base and at `/mcp`. A 401 or a network failure ends the search.
4. The key header is set on the HTTP client itself, so it rides on the SSE
   connect and on every message POST (and every streamable POST). The mock hub
   records this per request and `tests/test_mcp_hub_modes.py` asserts it.

Every failure is a typed exception (`backend/mcp_client/errors.py`) with a
`reason`: `not_configured`, `dns`, `tls`, `connect`, `timeout`, `budget`,
`http_status`, `no_transport`, `auth_rejected`, `tool_error`, `breaker_open`.

`GET /health` shows `mcp_hub`: `configured`, `enabled`, `transport` (`sse` or
`streamable-http`, null before the first call), `reachable`, `auth`
(`accepted`, `rejected` or `unknown`), `checked_at` (UTC ISO time of the last
success or failure), `last_error`, `last_error_reason`, `breaker_open`,
`breaker_seconds_left`, `tools`.

## 2. The check command

```
python -m backend.mcp_client.check               from the repository root
cd backend; python -m mcp_client.check           from backend/
  --json               machine readable report
  --timeout SECONDS    per step timeout (default MCP_HUB_TIMEOUT_SECONDS)
  --call-read-only     also call, once each, search_knowledgebase_tool,
                       compare_package (skipped: it needs arguments the check
                       cannot guess) and get_function_latencies. The list is
                       fixed in the code; nothing else is ever called
```

It prints the base URL, header name, whether a key is set and its length (never
the key), DNS, TCP and TLS, what SSE at `/sse` and streamable HTTP at `/mcp` and
at the base answered (status, content type, first SSE event, and the response
headers `mcp-session-id`, `www-authenticate`, `allow` when present), one probe of
`/sse` without the key, the tools (name and first description line) and a one line
verdict. The exit code is 0 only for `working`.

### Diagnosis table

| Verdict | What the check saw | Ask the hub administrator |
|---|---|---|
| `working` | A tool list came back through `HubClient` | nothing |
| `not_configured` | No key, or `MCP_HUB_ENABLED=false` | nothing: set `MCP_HUB_API_KEY` in `backend/.env` |
| `unreachable` | DNS failure, refused or timed out connection, TLS failure, all probes timed out, or HTTP 5xx | Is the hub running, and may this machine's network reach it (DNS name, firewall, certificate)? |
| `wrong_path` | `/sse`, `/mcp` and the base URL all answered 404 | The exact MCP endpoint URL and its transport |
| `wrong_transport` | A path answers (200, 400, 405, 406) but not as MCP SSE or streamable HTTP | Which MCP transport the hub speaks, and at which path |
| `auth_rejected` | 401 or 403 with the key (the report says which) | Is the key valid and active, which header carries it, and must this machine's address be allowed? |

## 3. The 24 tools

Listed on 2026-10-13 through the application's own client. "Customer" means the
access policy (`backend/access/policy.json`) treats it as needing a verified
session; "public" tools carry no customer data. `telephone_no` is a string, written
by the app as `0112150026` (the gate rewrites `+94` to `0`).

### Sign in

| Tool | Purpose | Arguments | Kind | Mutating |
|---|---|---|---|---|
| `validate_customer_and_send_otp` | Step 1: validate a connection number and SEND an OTP in the same call | `telephone_no`, `otp_media` (int, default 2) | customer | yes: sends a real SMS |
| `verify_otp_and_authenticate` | Step 2: verify the OTP and authenticate | `telephone_no`, `session_id`, `otp_value` | customer | yes: creates hub session state |
| `refresh_customer_token` | Force a token refresh | `telephone_no` | customer | yes: session state |
| `authenticate_with_token` | Seed session state from tokens issued out of band | `telephone_no`, `access_token`, `refresh_token`, `subscriber_id`, `account_number`, `expires_at` | customer | yes: session state |

### Postpaid and fixed line, read only

| Tool | Purpose | Arguments | Kind | Mutating |
|---|---|---|---|---|
| `show_gb_balance` | Data balance and usage summary | `telephone_no` | customer | no |
| `show_extra_gb_packages` | Extra GB packages and prices | `telephone_no`, `package_name` | customer | no |
| `show_daily_usage` | This month's daily usage | `telephone_no`, `bill_date` (int) | customer | no |
| `show_bill_history` | Last bill and 3 month history | `telephone_no` | customer | no |
| `show_last_payment` | Last payment | `telephone_no` | customer | no |
| `show_vas_data_bundle_packages` | VAS data bundle add ons | `telephone_no`, `package_name` | customer | no |
| `show_service_detail` | Service details (Fibre, Copper, LTE) | `telephone_no` | customer | no |
| `show_fault_list` | Faults already logged | `telephone_no` | customer | no |

### Postpaid and fixed line, changing things

| Tool | Purpose | Arguments | Kind | Mutating |
|---|---|---|---|---|
| `purchase_extra_gb` | Buy an extra GB package (postpaid) | `telephone_no`, `package_id` (int) | customer | yes: charges |
| `add_vas_data_bundle` | Subscribe to a VAS bundle (postpaid) | `telephone_no`, `package_id` (int) | customer | yes: charges |
| `log_fault` | Create a fault or complaint | `telephone_no`, `service_id`, `service_option`, optional `contact_no`, `nature_of_fault` | customer | yes: opens a ticket |

### LTE prepaid

| Tool | Purpose | Arguments | Kind | Mutating |
|---|---|---|---|---|
| `show_lte_subscriber_status` | Subscriber status | `telephone_no` | customer | no |
| `show_lte_wallet_detail` | Wallet or balance | `telephone_no` | customer | no |
| `show_lte_purchased_packages` | Active packages and add ons | `telephone_no` | customer | no |
| `show_reload_history` | Reload and transaction history | `telephone_no`, `from_date`, `to_date` (strings) | customer | no |
| `show_lte_prepaid_package_list` | Available main packages or add ons | `telephone_no`, `package_type` | customer | no |
| `subscribe_lte_prepaid_package` | Subscribe or unsubscribe a package | `telephone_no`, `product_id`, `offer_name`, optional `operation_type` (default `ADD_OFFERING`) | customer | yes: charges |

### General

| Tool | Purpose | Arguments | Kind | Mutating |
|---|---|---|---|---|
| `search_knowledgebase_tool` | Search the SLT knowledge base | `query`, optional `top_k` (default 3) | public | no |
| `compare_package` | Compare a current package with upgrade options | `current_package_name`, `current_price` (number), optional `top_k` (default 5) | public | no |
| `get_function_latencies` | Latency class of every tool | none | public | no |

## 4. Mapping to the app's customer lookup, and the gaps

The wizard (`backend/verification/service.py`) needs, for a landline typed by the
person, a `CustomerRecord` through the `CustomerLookup` protocol in
`backend/verification/lookup.py`: `find(phone_e164)` returning the display name
(to check the name the person typed), the registered mobile and email (shown
only masked), and the preferred language; and `get(record_id)`. The wizard then
sends its own code. `otp.hub_backend` (`OTP_BACKEND=hub`) instead uses the two
sign in tools above. `MCP_HUB_CUSTOMER_LOOKUP_TOOL` names a tool for the
lookup; `tools.lookup_customer_via_hub` and `mcp_client.customer_lookup` use it.

| Need | Hub today | Status |
|---|---|---|
| Find a customer by telephone number and get name, mobile, email, language | Only implicit: `validate_customer_and_send_otp` accepts or rejects a number, but it also sends an SMS and returns no name or contacts | GAP |
| Find a customer by name | None | GAP |
| Find a customer by registered mobile number | None | GAP |
| Find a customer by registered email | None | GAP |
| Send an SMS or email code on its own | None. Sending exists only inside `validate_customer_and_send_otp` | GAP |
| Verify a code the hub sent | `verify_otp_and_authenticate` | available (hub OTP only) |
| Customer data after sign in (balance, bills, faults, packages) | the `show_*` tools | available |

Consequence: with the hub alone the wizard cannot check a name, cannot offer
masked contact methods, and cannot choose the channel. That is why the customer
data source stays `local` (ADR 3.1: local customer directory plus local OTP).
Calling `validate_customer_and_send_otp` just to test whether a number exists
would text a real customer, so it is never used as a lookup.

### What to ask the hub administrator for

1. A read only lookup tool, for example `lookup_customer(telephone_no)`, that
   returns JSON: `found` (bool), `display_name`, `mobile` (the registered mobile),
   `email`, `preferred_language` (`en`, `si` or `ta`), and nothing else (no
   account numbers, no balances). `found: false` for an unknown number. If the hub
   will not return full contact details, return them already masked, plus a
   stable `contact_id` per method, and accept that id in the send tool.
2. A separate send tool, for example `send_otp(telephone_no, channel, contact_id)`,
   that sends a code by `sms` or `email` and returns a session id, with the code
   length, lifetime and attempt limit documented, and a matching verify tool. Or
   permission for the app to send its own code and use only the lookup.
3. The documented format of `telephone_no` (national `0112345678`, or `+94...`)
   and the exact shape of every response (the app parses defensively because
   response shapes are not documented).
4. Rate limits, and whether a test number that does not send real SMS exists.

When the lookup tool exists: set `MCP_HUB_CUSTOMER_LOOKUP_TOOL=<its name>`
and `CUSTOMER_DATA_SOURCE=mcp_hub`. While `mcp_hub` is selected and no tool is
named or listed, the lookup reports "unavailable" (`CustomerLookupUnavailable`),
never `invalid_details`, so a missing tool is not mistaken for every customer
being wrong.

## 5. Registering the hub in an MCP client

Register it with the sse transport, the URL `https://mcphub.sltdigitallab.lk/sse`
and the header `X-API-Key: <key>`. The URL must end in `/sse`. A registration
that uses the streamable http transport at the root URL fails ("MCP endpoint not
found") because the hub has no streamable HTTP endpoint (`POST /` and `POST /mcp`
give 404). Keep the key out of shell history and shared configuration where you
can.

## 6. Not verified

* No customer or mutating tool has been called on the real hub, so tool
  response shapes (and the format `telephone_no` really accepts) are unverified.
* `--call-read-only` was tested against the mock hub only.
* The mapping of "customer" and "public" follows `backend/access/policy.json`,
  not the hub's own documentation.


## 7. How the assistant uses the hub for account answers (2026-10-13)

Read only tools only, for a verified customer's own number (the access gate forces
it): show_gb_balance, show_lte_wallet_detail, show_lte_purchased_packages,
show_bill_history, show_last_payment, show_reload_history, show_service_detail,
show_lte_subscriber_status, show_fault_list, show_extra_gb_packages and
show_vas_data_bundle_packages. Never called: purchase_extra_gb, add_vas_data_bundle,
log_fault, subscribe_lte_prepaid_package, authenticate_with_token,
refresh_customer_token (the code refuses them even if asked), and not used yet:
show_daily_usage, show_lte_prepaid_package_list, compare_package.

The hub signs a customer in through its own one time code, so the wizard has a hub
mode (VERIFY_OTP_BACKEND=hub) that uses validate_customer_and_send_otp and
verify_otp_and_authenticate. With the local code the hub has no session for the
customer and the customer tools answer "not authenticated".

What to ask the hub administrator, now that this is built: (1) the exact response of
each read only tool for one test number, (2) what otp_media values mean and the format
of telephone_no, (3) how "not signed in" and "not a customer" are reported, (4) a test
number whose code goes to a phone you control, so this can be tried live.


## 8. The gateway and the hub as the only source (2026-10-13)

The assistant's calls now go through `backend/mcp_gateway` (see docs/MCP_INTEGRATION_PLAN.md
and docs/MCP_TOOLS.md). Two facts from the live hub drive the design: the login belongs to one
open connection (so the gateway holds one per verified customer), and `otp_media` is 1 SMS,
2 email, 3 voice (so the wizard sends the channel the customer picked, not the default).

To ask the hub administrator: (1) why `show_service_detail` answers `405 Method Not Allowed`
for a working line and whether the other customer tools work for that line; (2) the real
response of each read tool and of the package lists; (3) whether an idempotency key or a
duplicate guard exists for purchases and fault logging; (4) the hub's own OTP expiry and
attempt limits; (5) the format of `from_date` and `to_date` for `show_reload_history` and the
allowed `package_type` values.
