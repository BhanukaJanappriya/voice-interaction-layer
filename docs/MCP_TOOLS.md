# SLT MCP hub tool reference

Generated 2026-10-13 from a live `tools/list` on the hub (legacy SSE transport, `X-API-Key`). 24 tools.
Every output schema is `{"result": string}`: the payload is text, usually JSON inside the string (the knowledge base returns `{query, chunks:[{content, score}], count}`). Failures come back as a normal result (`isError` false) with `{"success": false, "error": "..."}`; observed live: `No active session for <number>. Ask the customer to authenticate: call validate_customer_and_send_otp then verify_otp_and_authenticate.`

## Findings that shape the design

1. **No tool takes an access token.** Customer tools take only `telephone_no`. The hub keeps the token in "the session" after `verify_otp_and_authenticate` and refreshes it itself. The one way to pass tokens is `authenticate_with_token(telephone_no, access_token, refresh_token, subscriber_id, account_number, expires_at)`, meant for tokens issued by the hub's REST endpoints `/api/otp/send` and `/api/otp/verify`, which are not exposed as tools.
2. **OTP channel can be chosen:** `otp_media` 1 = SMS, 2 = email (the default), 3 = voice call. The tool sends at once; there is no call that lists channels without sending. The masked contact comes back only in the send result ("along with masked contact info"). So step 2 of the wizard can offer the three channels with generic labels, and the masked contact can only be shown after sending.
3. **verify_otp_and_authenticate** returns subscriber_id, account_number, package info and privileges (text). It does not document returning the access token, refresh token or expiry, so we cannot keep or inject tokens ourselves; the hub holds them.
4. **Measured 2026-10-13 with an owner number (SMS code): the hub login belongs to the MCP connection.** After `verify_otp_and_authenticate` succeeded on connection A, a second connection opened while A was still open, and a third opened after A closed, both got `No active session`. So verify and every later customer tool must run on the same open connection, and that connection must be kept for as long as the customer is verified. Observed too: `validate_customer_and_send_otp` returns `contact_info` as a Python style string with the masked mobile and the masked email, so both channels are known after one send; a number the hub cannot look up returns `success false` with a .NET null reference error and sends nothing; the verify result has keys success, subscriber_id, account_number, subscriber_package, privileges, note (no token); `show_service_detail` on the live hub returned an upstream `405 Method Not Allowed` text for this account (a hub side fault, not ours).
5. **get_function_latencies** is a static classification (high / mid / low per tool, e.g. all four state changing tools and the OTP tools are high), not measured latency. It is for developers and the admin portal, not customers.

## Tool matrix

| Tool | Required args | Optional args | Auth needed | Effect |
|---|---|---|---|---|
| validate_customer_and_send_otp | telephone_no | otp_media | none (starts login) | sends a real SMS, email or call |
| verify_otp_and_authenticate | telephone_no, session_id, otp_value | none | none (finishes login) | creates the hub session (token) |
| refresh_customer_token | telephone_no | none | hub session for telephone_no | renews the hub token |
| authenticate_with_token | telephone_no, access_token, refresh_token, subscriber_id, account_number, expires_at | none | none (seeds login) | seeds the hub session from tokens |
| show_gb_balance | telephone_no | none | hub session for telephone_no | read only |
| show_extra_gb_packages | telephone_no, package_name | none | hub session for telephone_no | read only |
| purchase_extra_gb | telephone_no, package_id | none | hub session for telephone_no | changes the account, charges money |
| show_daily_usage | telephone_no, bill_date | none | hub session for telephone_no | read only |
| show_bill_history | telephone_no | none | hub session for telephone_no | read only |
| show_last_payment | telephone_no | none | hub session for telephone_no | read only |
| show_vas_data_bundle_packages | telephone_no, package_name | none | hub session for telephone_no | read only |
| add_vas_data_bundle | telephone_no, package_id | none | hub session for telephone_no | changes the account, charges money |
| show_service_detail | telephone_no | none | hub session for telephone_no | read only |
| show_fault_list | telephone_no | none | hub session for telephone_no | read only |
| log_fault | telephone_no, service_id, service_option | contact_no, nature_of_fault | hub session for telephone_no | creates a fault ticket |
| show_lte_subscriber_status | telephone_no | none | hub session for telephone_no | read only |
| show_lte_wallet_detail | telephone_no | none | hub session for telephone_no | read only |
| show_lte_purchased_packages | telephone_no | none | hub session for telephone_no | read only |
| show_reload_history | telephone_no, from_date, to_date | none | hub session for telephone_no | read only |
| show_lte_prepaid_package_list | telephone_no, package_type | none | hub session for telephone_no | read only |
| subscribe_lte_prepaid_package | telephone_no, product_id, offer_name | operation_type | hub session for telephone_no | changes the account, charges money |
| search_knowledgebase_tool | query | top_k | none | read only |
| compare_package | current_package_name, current_price | top_k | none documented | read only |
| get_function_latencies | none | none | none | read only |

Semantics of arguments taken from the descriptions: `show_daily_usage.bill_date` is a day of month 1 to 31; `show_reload_history` takes `from_date` and `to_date` strings (format not documented); `log_fault.service_option` is BB, VOICE, PEOTV or OTHER and `service_id` comes from `show_service_detail` (listofBBSService, listofVoiceService, listofPEOService); `purchase_extra_gb.package_id` comes from `show_extra_gb_packages`; `add_vas_data_bundle.package_id` from `show_vas_data_bundle_packages`. No tool documents an idempotency key.

## Full schemas

### validate_customer_and_send_otp

```text
Step 1: Validate an SLT customer connection number and send an OTP.

Args:
    telephone_no: The customer's telephone/connection number (e.g. "0112150026").
    otp_media: OTP delivery channel — 1=SMS, 2=email (default), 3=voice call.

Returns the session_id needed for step 2, along with masked contact info.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "otp_media": {
   "default": 2,
   "title": "Otp Media",
   "type": "integer"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "validate_customer_and_send_otpArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "validate_customer_and_send_otpOutput",
 "type": "object"
}
```

### verify_otp_and_authenticate

```text
Step 2: Verify the OTP and authenticate the customer.

Stores the access token in the session — subsequent API tools only need
telephone_no, not the raw token.

Args:
    telephone_no: The customer's telephone number used in step 1.
    session_id: The session_id returned from validate_customer_and_send_otp.
    otp_value: The 6-digit OTP the customer received.

Returns subscriber_id, account_number, package info, and privileges.
The token is managed automatically (including refresh when it expires).
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "session_id": {
   "title": "Session Id",
   "type": "string"
  },
  "otp_value": {
   "title": "Otp Value",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "session_id",
  "otp_value"
 ],
 "title": "verify_otp_and_authenticateArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "verify_otp_and_authenticateOutput",
 "type": "object"
}
```

### refresh_customer_token

```text
Manually force a token refresh for a customer.

Normally the token is refreshed automatically before it expires.
Call this only if you receive an authentication error from another tool.

Args:
    telephone_no: The customer's telephone number.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "refresh_customer_tokenArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "refresh_customer_tokenOutput",
 "type": "object"
}
```

### authenticate_with_token

```text
Seed this session's authentication state from tokens issued out-of-band.

Use this instead of validate_customer_and_send_otp/verify_otp_and_authenticate
when the customer already authenticated externally — e.g. a browser frontend
ran the OTP flow via the /api/otp/send + /api/otp/verify REST endpoints, and a
LiveKit voice agent is reading the resulting tokens from room/participant
metadata after connecting.

Args:
    telephone_no: The customer's telephone number.
    access_token: SLT access token from /api/otp/verify.
    refresh_token: SLT refresh token from /api/otp/verify.
    subscriber_id: Subscriber ID from /api/otp/verify.
    account_number: Account number from /api/otp/verify.
    expires_at: Unix timestamp (seconds) when access_token expires, as
                returned by /api/otp/verify.

After this call, all other tools work with just telephone_no, exactly as
if verify_otp_and_authenticate had been called in this same session.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "access_token": {
   "title": "Access Token",
   "type": "string"
  },
  "refresh_token": {
   "title": "Refresh Token",
   "type": "string"
  },
  "subscriber_id": {
   "title": "Subscriber Id",
   "type": "string"
  },
  "account_number": {
   "title": "Account Number",
   "type": "string"
  },
  "expires_at": {
   "title": "Expires At",
   "type": "number"
  }
 },
 "required": [
  "telephone_no",
  "access_token",
  "refresh_token",
  "subscriber_id",
  "account_number",
  "expires_at"
 ],
 "title": "authenticate_with_tokenArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "authenticate_with_tokenOutput",
 "type": "object"
}
```

### show_gb_balance

```text
Get the current GB data balance and usage summary for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).

Returns package summary with used/remaining GB, bonus data, and package info.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_gb_balanceArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_gb_balanceOutput",
 "type": "object"
}
```

### show_extra_gb_packages

```text
Get available extra GB packages and their prices for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    package_name: Current package name (e.g. "WEB FAMILY PLUS") from show_gb_balance.

Returns list of packages with packageId, volume (GB), price, tax, and validity.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "package_name": {
   "title": "Package Name",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "package_name"
 ],
 "title": "show_extra_gb_packagesArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_extra_gb_packagesOutput",
 "type": "object"
}
```

### purchase_extra_gb

```text
Purchase an extra GB package on postpaid for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    package_id: Package ID to purchase — get from show_extra_gb_packages.

Returns purchase status and confirmation message.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "package_id": {
   "title": "Package Id",
   "type": "integer"
  }
 },
 "required": [
  "telephone_no",
  "package_id"
 ],
 "title": "purchase_extra_gbArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "purchase_extra_gbOutput",
 "type": "object"
}
```

### show_daily_usage

```text
Get the current month's daily data usage for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    bill_date: Day of month to retrieve usage from (1–31).

Returns a daily usage list with date, total usage, standard usage, and free usage.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "bill_date": {
   "title": "Bill Date",
   "type": "integer"
  }
 },
 "required": [
  "telephone_no",
  "bill_date"
 ],
 "title": "show_daily_usageArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_daily_usageOutput",
 "type": "object"
}
```

### show_bill_history

```text
Get the last bill details and bill history for the past 3 months.

Args:
    telephone_no: The customer's telephone number (must be authenticated).

Returns billing cycles with billMonth, billValue, payments, outstanding, and bill codes.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_bill_historyArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_bill_historyOutput",
 "type": "object"
}
```

### show_last_payment

```text
Get the last payment information for a customer account.

Args:
    telephone_no: The customer's telephone number (must be authenticated).

Returns billAmount, lastBilledDate, lastPaymentAmount, and outstandingBalance.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_last_paymentArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_last_paymentOutput",
 "type": "object"
}
```

### show_vas_data_bundle_packages

```text
Get available VAS data bundle add-on packages for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    package_name: Current package name from show_gb_balance.

Returns categories of add-on packages with id, name, price, tax, and validity.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "package_name": {
   "title": "Package Name",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "package_name"
 ],
 "title": "show_vas_data_bundle_packagesArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_vas_data_bundle_packagesOutput",
 "type": "object"
}
```

### add_vas_data_bundle

```text
Subscribe to a VAS data bundle add-on on postpaid.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    package_id: Package ID to subscribe — get from show_vas_data_bundle_packages.

Returns subscription status and confirmation.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "package_id": {
   "title": "Package Id",
   "type": "integer"
  }
 },
 "required": [
  "telephone_no",
  "package_id"
 ],
 "title": "add_vas_data_bundleArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "add_vas_data_bundleOutput",
 "type": "object"
}
```

### show_service_detail

```text
Get service details for a customer (Fibre, Copper, or LTE Prepaid/Postpaid).

Args:
    telephone_no: The customer's telephone number (must be authenticated).

Returns accountNo, package info, and lists of Voice, Broadband, and PeoTV services.
Service IDs from this response are needed to create fault requests.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_service_detailArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_service_detailOutput",
 "type": "object"
}
```

### show_fault_list

```text
Get the list of already logged faults/complaints for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).

Returns list of faults with faultId, status, date, and serviceName.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_fault_listArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_fault_listOutput",
 "type": "object"
}
```

### log_fault

```text
Create a new fault/complaint request for a customer.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    service_id: Service ID from show_service_detail — use listofBBSService for BB,
                listofVoiceService for Voice, listofPEOService for PeoTV.
    service_option: Type of service — "BB" (Broadband), "VOICE", "PEOTV", or "OTHER".
    contact_no: Optional callback contact number.
    nature_of_fault: Optional description of the fault.

Returns fault reference number and confirmation message.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "service_id": {
   "title": "Service Id",
   "type": "string"
  },
  "service_option": {
   "title": "Service Option",
   "type": "string"
  },
  "contact_no": {
   "default": "",
   "title": "Contact No",
   "type": "string"
  },
  "nature_of_fault": {
   "default": "",
   "title": "Nature Of Fault",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "service_id",
  "service_option"
 ],
 "title": "log_faultArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "log_faultOutput",
 "type": "object"
}
```

### show_lte_subscriber_status

```text
Get the LTE prepaid subscriber status.

Args:
    telephone_no: The customer's 11-digit LTE SLT number (must be authenticated).

Returns statusName (Active/Idle/Suspended/Disable/Pool) and statusExpireTime.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_lte_subscriber_statusArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_lte_subscriber_statusOutput",
 "type": "object"
}
```

### show_lte_wallet_detail

```text
Get the LTE prepaid wallet/account balance.

Args:
    telephone_no: The customer's 11-digit LTE SLT number (must be authenticated).

Returns totalAmount (divide by 100 for LKR), reservedAmount, and balance details.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_lte_wallet_detailArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_lte_wallet_detailOutput",
 "type": "object"
}
```

### show_lte_purchased_packages

```text
Get all packages and add-ons currently active for an LTE prepaid customer.

Args:
    telephone_no: The customer's 11-digit LTE SLT number (must be authenticated).

Returns list of free units with packageName, packageCategory, and expiry info.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  }
 },
 "required": [
  "telephone_no"
 ],
 "title": "show_lte_purchased_packagesArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_lte_purchased_packagesOutput",
 "type": "object"
}
```

### show_reload_history

```text
Get the reload/transaction history for an LTE prepaid customer.

Args:
    telephone_no: The customer's 11-digit LTE SLT number (must be authenticated).
    from_date: Start date in YYYYMMDD format (e.g. "20230101"). Max 1 month prior.
    to_date: End date in YYYYMMDD format (e.g. "20230601"). Use today's date by default.

Returns transactions with txnId, txnType, txnAmount, txnTime.
Check statusCode == 1001 for successful transactions.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "from_date": {
   "title": "From Date",
   "type": "string"
  },
  "to_date": {
   "title": "To Date",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "from_date",
  "to_date"
 ],
 "title": "show_reload_historyArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_reload_historyOutput",
 "type": "object"
}
```

### show_lte_prepaid_package_list

```text
Get the list of available LTE prepaid main packages or add-ons.

Args:
    telephone_no: The customer's telephone number (must be authenticated).
    package_type: "Main Packages" or "Add-ons".

Returns packages with OFFERING_ID, OFFERING_NAME, DATA_VOLUME_GB,
PRICE_LKR_WITH_TAX, and VALIDITY.
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "package_type": {
   "title": "Package Type",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "package_type"
 ],
 "title": "show_lte_prepaid_package_listArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "show_lte_prepaid_package_listOutput",
 "type": "object"
}
```

### subscribe_lte_prepaid_package

```text
Subscribe or unsubscribe an LTE prepaid package or add-on.

Pre-requisites (verify before calling):
1. Customer status must be "Active" (use show_lte_subscriber_status).
2. Wallet balance >= package price (use show_lte_wallet_detail + show_lte_prepaid_package_list).

Args:
    telephone_no: The customer's 11-digit LTE SLT number (must be authenticated).
    product_id: OFFERING_ID from show_lte_prepaid_package_list.
    offer_name: OFFERING_NAME from show_lte_prepaid_package_list.
    operation_type: "ADD_OFFERING" to subscribe (default).
```

Input schema:
```json
{
 "properties": {
  "telephone_no": {
   "title": "Telephone No",
   "type": "string"
  },
  "product_id": {
   "title": "Product Id",
   "type": "string"
  },
  "offer_name": {
   "title": "Offer Name",
   "type": "string"
  },
  "operation_type": {
   "default": "ADD_OFFERING",
   "title": "Operation Type",
   "type": "string"
  }
 },
 "required": [
  "telephone_no",
  "product_id",
  "offer_name"
 ],
 "title": "subscribe_lte_prepaid_packageArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "subscribe_lte_prepaid_packageOutput",
 "type": "object"
}
```

### search_knowledgebase_tool

```text
Search the SLT knowledge base for information about packages, services, and other SLT topics.

Use this tool to find:
- Package details and pricing information
- Service features and benefits
- How-to guides and FAQs
- Problem solutions and troubleshooting
- Any general SLT product or service information

The query can be:
- Keywords: "fiber packages", "data add-ons", "voice plans"
- Questions: "How do I check data balance?", "What are PEO TV packages?"
- Package names: "Web Family Plus", "Gaming Bundle"

Args:
    query: Search query — keywords, questions, or package names.
    top_k: Number of top matching results to return (default: 3, max recommended: 10).

Returns knowledge base search results with:
- query: The search query
- chunks: List of matching content chunks with:
    * content: The relevant information from the knowledge base
    * score: Relevance score (0-1, higher = more relevant)
- count: Total number of results returned
```

Input schema:
```json
{
 "properties": {
  "query": {
   "title": "Query",
   "type": "string"
  },
  "top_k": {
   "default": 3,
   "title": "Top K",
   "type": "integer"
  }
 },
 "required": [
  "query"
 ],
 "title": "search_knowledgebase_toolArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "search_knowledgebase_toolOutput",
 "type": "object"
}
```

### compare_package

```text
Compare the customer's current package against available upgrade options.

Always recommends packages priced HIGHER than the current one — use this
when a customer asks to compare packages, upgrade, or "what's better than
my current plan". Never suggest a package priced the same as or lower
than current_price.

Args:
    current_package_name: The customer's current package name (from
                           show_gb_balance or show_service_detail).
    current_price: The customer's current package monthly price in LKR.
    top_k: Number of knowledge base results to consider (default: 5).

Returns the current package context, knowledge base content about
higher-tier packages, and an explicit instruction to only recommend
packages priced above current_price.
```

Input schema:
```json
{
 "properties": {
  "current_package_name": {
   "title": "Current Package Name",
   "type": "string"
  },
  "current_price": {
   "title": "Current Price",
   "type": "number"
  },
  "top_k": {
   "default": 5,
   "title": "Top K",
   "type": "integer"
  }
 },
 "required": [
  "current_package_name",
  "current_price"
 ],
 "title": "compare_packageArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "compare_packageOutput",
 "type": "object"
}
```

### get_function_latencies

```text
Return the latency classification for every registered MCP tool.

Latency levels:
- high: involves OTP delivery, external auth flows, or write/purchase operations
- mid:  standard single API call to an SLT backend service
- low:  local computation only, no network calls

Tools without an explicit latency annotation are classified as "low" by default.

Returns a summary grouped by latency level, plus a per-tool flat list.
```

Input schema:
```json
{
 "properties": {},
 "title": "get_function_latenciesArguments",
 "type": "object"
}
```
Output schema:
```json
{
 "properties": {
  "result": {
   "title": "Result",
   "type": "string"
  }
 },
 "required": [
  "result"
 ],
 "title": "get_function_latenciesOutput",
 "type": "object"
}
```
