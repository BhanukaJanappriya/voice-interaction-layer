# MCP hub as the only source of truth: integration plan

Written 2026-10-13 (Phase 0). Tool facts are in docs/MCP_TOOLS.md, generated from the live hub. Nothing in this plan has been run against a real customer: the only live calls made were `tools/list`, `get_function_latencies`, `search_knowledgebase_tool` and one `show_gb_balance` for a fake number (it answered "No active session").

## 1. What Phase 0 found

| Question | Answer | Source |
|---|---|---|
| Can the OTP channel be chosen? | Yes. `otp_media` 1 SMS, 2 email (default), 3 voice call. | tool description |
| Are channels or masked contacts returned before sending? | No. The tool sends at once and returns `session_id` plus masked contact info. | tool description |
| Does verify return a token? | It stores the token in the hub session and returns subscriber_id, account_number, package info, privileges (text). No token, expiry or refresh token is documented as returned. | tool description |
| How is the token passed to other tools? | It is not. Customer tools take only `telephone_no`. `authenticate_with_token` can seed a session from tokens issued by the hub's REST `/api/otp/*`, which we have no access to. | schemas |
| Which tools by service type? | Postpaid or fibre: show_gb_balance, show_extra_gb_packages, purchase_extra_gb, show_daily_usage, show_bill_history, show_last_payment, show_vas_data_bundle_packages, add_vas_data_bundle. LTE prepaid: show_lte_*, show_reload_history, show_lte_prepaid_package_list, subscribe_lte_prepaid_package. Common: show_service_detail, show_fault_list, log_fault, compare_package, search_knowledgebase_tool. | descriptions |
| get_function_latencies | A static high, mid, low label per tool. For developers and the admin page, not customers. | live call |
| Error format | A normal result (not an MCP error) with `{"success": false, "error": "..."}`. | live call |
| Idempotency key | None on any state changing tool. | schemas |

### Two defects in the existing code that this exposes

1. `backend/otp/hub_backend.py` sends `otp_media=2`, which is **email**, while the wizard's hub mode tells the customer the code goes to their registered mobile number. Fix: send the chosen channel.
2. `HubClient` opens a new MCP connection for every call. Confirmed live that the hub binds the login to the connection, so today's hub mode and account answers can never work on the real hub: every customer tool after verify gets "No active session". The gateway fixes this with a long lived connection per verified customer.

## 2. Tool access matrix (config/tool_access.yaml, server enforced)

| Tier | Tools | Called by |
|---|---|---|
| guest | search_knowledgebase_tool | assistant |
| authenticating | validate_customer_and_send_otp, verify_otp_and_authenticate | wizard endpoints only, never the model |
| authenticated | all customer tools filtered by service type (section 3) | assistant, state changing ones only after confirmation |
| diagnostic | get_function_latencies | admin page and /health; customers only if EXPOSE_LATENCY_TOOL |
| never | authenticate_with_token, refresh_customer_token | refused (the hub refreshes itself) |

The gateway checks tier, then allowlist, then service type, then forces `telephone_no` to the verified number, applies the budget, then writes an audit line (session id, tool, outcome, milliseconds; no arguments).

## 3. Service type filtering

Learned from `show_service_detail` (cached 10 minutes, already built in customer_service). Fixed: postpaid set. LTE prepaid: LTE set. Unknown: only the common set.

## 4. OTP flow mapping

1. Step 1 validates the format locally and sends nothing.
2. Step 2 shows SMS, email and voice call as radio options with generic labels (the hub reveals the masked contact only after sending). "Send OTP" calls the hub with the chosen `otp_media`.
3. Step 3 shows the masked contact the hub returned, and verifies. Our rules (120 s, 3 attempts, single use, resend wait, rate limits) stay on top; the hub is authoritative when stricter.
4. A hub failure is one generic localized message and never says whether the number exists (already built).

## 5. Token lifecycle

The hub owns the token. We keep only the verified flag in our cookie (amr otp, hub) and the verified number. On a tool answer that means "no active session" the session is downgraded to guest, the customer is told in their language, and offered verification again. Logout, session end and restart clear our state. Whether the hub also needs a close is unknown.

## 6. Answering per tier

Gemini and LiveKit Inference: native function calling with only the session's allowed tool schemas. Local Llama: a deterministic multilingual router (already in customer_service for read tools) picks tools, Llama only phrases the result. Same gateway everywhere, 4 tool calls per turn at most.

## 7. State changing tools

purchase_extra_gb, add_vas_data_bundle, subscribe_lte_prepaid_package and log_fault: a pending action with a one time token and a 60 second expiry, shown in a modal in the existing style, confirmed by button or by a clear spoken yes, executed once, never retried, and the hub's result reported exactly. No idempotency key exists, so duplicate protection is ours (one token, one execution).

## 8. Decisions, risks and conflicts needing the owner

| # | Item | Position |
|---|---|---|
| ADR-1 | Send the chosen `otp_media`, never the default | accepted; fixes defect 1 |
| ADR-2 | Gateway wraps the existing HubClient and customer_service allowlist | accepted |
| ADR-3 | Local knowledge base kept but disabled by `ANSWER_SOURCE=mcp_hub` | accepted; this changes guest answers |
| ADR-4 | Customer tokens never reach us, so "inject token" becomes "force telephone_no and keep the right hub session" | forced by the schemas |
| Q1 | Is the hub login per connection or per number? | **Answered live: per connection.** The gateway must hold one open hub connection per verified customer, used for verify and all later tools |
| Q2 | Tool results go to the cloud LLM (brief R4) but you earlier chose local Llama only for customer data | the flag CUSTOMER_DATA_TO_CLOUD_LLM stays false until you say otherwise |
| Q3 | Purchases and fault logging were "read only for now" | this brief overrides it, but nothing will be run for real without your explicit approval |
| Q4 | Guests lose the local knowledge base answers | confirm ANSWER_SOURCE=mcp_hub as the default |

Risks: response shapes of customer tools are still unobserved (outputs are free text); confirmation relies on `show_*` data that may not contain price and validity fields; voice yes detection in three languages is machine written.

## 9. Ownership and contracts for the subagents

A: backend/mcp_gateway/**. B: backend/verification/** and frontend/verify/** (channel choice). C: backend/tool_agent/**. D: backend/confirmations/**, frontend/ui/confirm/**. E: tests. Shared files are edited only by the lead. Contract: `gateway.call(name, arguments, access) -> GatewayResult(kind ok|error|reauth|unavailable|refused, text, ms)`, `tool_schemas(access) -> list`, `pending.create(access, tool, arguments, summary) -> token`, `pending.confirm(access, token) -> GatewayResult`.

## 10. Test plan

Mocked hub built from these schemas (outputs as JSON strings, errors as `success false`): guest reaches only the knowledge base (including a model asking for another tool and a crafted request), guest account questions get the dial message in en, si, ta, OTP by each channel, wrong and expired code, downgrade on "no active session", service type filtering, every read answer equals the mocked data, no state change without confirmation, expiry, no automatic retry, hub down, no tokens or PII in logs and browser responses. Live checks need the owner's test number.


## 11. Status after the build (2026-10-13)

Built as planned, with these differences. (a) Cloud function calling is not used (section 6 of
KNOWN_LIMITATIONS): retrieval runs before the model for every tier. (b) State changing tools
exist but are off by default. (c) The OTP voice call channel is supported by the gateway and not
offered in the wizard. (d) The masked contact appears in step 3, because the hub only returns it
after sending. ADR-5: customer data stays on the local Llama (owner decision). ADR-6 (changed 2026-10-14 at the owner's request): a customer
whose service type cannot be learned is treated as postpaid, so a hub that fails `show_service_detail`
(as it did on the live line) still lets balance and bill questions through; the LTE prepaid tools stay
hidden until the type is known. ADR-7: the diagnostic tool is shown to operators only.
