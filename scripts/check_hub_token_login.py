"""One time check of the hub's REST sign in and of the authenticate_with_token tool, with a REAL phone.

!!! THIS SENDS A REAL SMS (or e-mail or call) TO THE NUMBER YOU TYPE. Run it once, with your own number. !!!

What it does, in order:
  1. asks for the number and for a clear yes, then POSTs {hub}/api/otp/send (the hub sends a real code);
  2. asks you to type the code, then POSTs {hub}/api/otp/verify (once, no retry);
  3. prints ONLY the structure of both answers: HTTP status, field names with their types, which token
     fields could be read, how many seconds the access token lives, and the masked contacts. No token, code,
     session id or number value is printed;
  4. opens a new MCP connection, calls authenticate_with_token with the tokens and prints what kind of answer
     came back (never its text), then calls the customer read tools and prints each tool name with ok or not;
  5. opens a second MCP connection WITHOUT seeding and shows that it has no login, then seeds it again.

It reads backend/.env for the hub URL and key like the app does. It writes nothing to disk, changes no
account and calls no state changing tool. Paste the output back to the developers: it holds no secret.

Run from the project root:   venv\\Scripts\\python.exe scripts\\check_hub_token_login.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import config  # noqa: E402,F401
from mcp_client.client import get_hub  # noqa: E402
from mcp_gateway import rest_auth  # noqa: E402
from mcp_gateway.session import HubSession  # noqa: E402

READ_TOOLS = (
    ("show_service_detail", {}),
    ("show_gb_balance", {}),
    ("show_bill_history", {}),
    ("show_last_payment", {}),
    ("show_fault_list", {}),
    ("show_lte_subscriber_status", {}),
    ("show_lte_wallet_detail", {}),
    ("show_lte_purchased_packages", {}),
)


def shape(node, path="", out=None, depth=0):
    """Field paths with type names only, e.g. 'data.access_token: str(812 chars)'. Never a value."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for key, value in node.items():
            shape(value, f"{path}.{key}" if path else str(key), out, depth + 1)
    elif isinstance(node, list):
        out.append(f"{path}: list[{len(node)}]")
        if node and depth < 4:
            shape(node[0], path + "[0]", out, depth + 1)
    elif isinstance(node, str):
        out.append(f"{path}: str({len(node)} chars)")
    else:
        out.append(f"{path}: {type(node).__name__}")
    return out


def show(title, status, data):
    print(f"\n== {title}")
    print(f"HTTP status: {status}")
    if data is None:
        print("body: not JSON or no answer")
        return
    for line in shape(data):
        print("  " + line)


def answer_kind(text, is_error):
    try:
        data = json.loads(text) if text.strip().startswith("{") else None
    except ValueError:
        data = None
    if is_error:
        return "tool error flag"
    if isinstance(data, dict):
        if data.get("success") is False or data.get("error"):
            return "json, success false or an error field"
        return "json, ok" + (f", keys: {', '.join(sorted(map(str, data)))[:120]}" if data else "")
    if text.startswith("Error"):
        return "text starting with Error"
    return f"text, {len(text)} chars"


def main() -> int:
    hub = get_hub()
    if not hub.configured:
        print("The hub is not configured (MCP_HUB_URL and MCP_HUB_API_KEY in backend/.env).")
        return 2
    print("WARNING: this sends a REAL code by SMS, e-mail or call to the number you type.")
    if input("Type SEND to continue, anything else stops: ").strip() != "SEND":
        print("Stopped, nothing was sent.")
        return 1
    number = input("Your own number, national form (for example 0771234567): ").strip()
    media = input("Channel 1 = SMS, 2 = e-mail, 3 = voice call [1]: ").strip() or "1"
    if media not in ("1", "2", "3"):
        print("Channel must be 1, 2 or 3.")
        return 1

    status, data = rest_auth._post(rest_auth.SEND_PATH, {"telephone_no": number, "otp_media": int(media)})
    show("POST /api/otp/send", status, data)
    layers = rest_auth._layers(data)
    session_id = rest_auth._text(rest_auth._pick(layers, ("session_id", "sessionid", "otp_session_id")))
    info = rest_auth._pick(layers, ("contact_info", "contactinfo", "contact", "contacts"), scalar=False)
    print("session id read:", "yes" if session_id else "NO")
    print("masked mobile:", rest_auth._contact(info, ("mobile", "masked_mobile", "phone")) if info is not None else "(none)")
    print("masked e-mail:", rest_auth._contact(info, ("email", "masked_email")) if info is not None else "(none)")
    if not session_id:
        print("No session id could be read, so the check stops here.")
        return 1

    code = input("Type the code you received: ").strip()
    status, data = rest_auth._post(
        rest_auth.VERIFY_PATH, {"telephone_no": number, "session_id": session_id, "otp_value": code}
    )
    show("POST /api/otp/verify", status, data)
    tokens = rest_auth.parse_tokens(data)
    layers = rest_auth._layers(data)
    print("\n== What the gateway could read")
    for label, names in (
        ("access token", ("access_token", "accesstoken", "access")),
        ("refresh token", ("refresh_token", "refreshtoken", "refresh")),
        ("subscriber id", ("subscriber_id", "subscriberid")),
        ("account number", ("account_number", "accountnumber", "account_no", "accountno")),
    ):
        print(f"  {label}: {'present' if rest_auth._pick(layers, names) is not None else 'MISSING'}")
    if tokens is None:
        print("  tokens: NOT readable (the gateway would fall back to the connection mode)")
        return 1
    print(f"  expires in: {int(tokens.expires_at - rest_auth._wall())} seconds from now")

    args = {
        "telephone_no": number,
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "subscriber_id": tokens.subscriber_id,
        "account_number": tokens.account_number,
        "expires_at": float(tokens.expires_at),
    }

    def seeded_session(label):
        session = HubSession()
        session.open()
        out = session.call("authenticate_with_token", args, 20)
        print(f"  authenticate_with_token on {label}: {answer_kind(out.text, out.is_error)}")
        return session

    print("\n== authenticate_with_token, then the customer read tools on that one connection")
    first = seeded_session("connection 1")
    try:
        for name, extra in READ_TOOLS:
            try:
                out = first.call(name, {"telephone_no": number, **extra}, 20)
                print(f"  {name}: {answer_kind(out.text, out.is_error)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {name}: failed ({type(exc).__name__})")
    finally:
        first.close()

    print("\n== a second connection that was NOT seeded")
    plain = HubSession()
    plain.open()
    try:
        out = plain.call("show_service_detail", {"telephone_no": number}, 20)
        print(f"  show_service_detail without a seed: {answer_kind(out.text, out.is_error)} (expected: no login)")
    finally:
        plain.close()

    print("\n== seeding a new connection again with the same tokens (the reseed the gateway does)")
    again = seeded_session("connection 3")
    try:
        out = again.call("show_service_detail", {"telephone_no": number}, 20)
        print(f"  show_service_detail after the second seed: {answer_kind(out.text, out.is_error)}")
    finally:
        again.close()
    print("\nDone. Nothing was stored. Send this output to the developers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
