"""Self check: python -m verification   (from backend/)

Runs the whole wizard in-process against a throwaway SQLite database in a
temporary folder, with a recording sender. Touches no real database, no SMTP,
no SMS gateway and no hub. Prints one line per check and the latency of each
step.
"""

import os
import sys
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="verification-selfcheck-")
os.environ["DATA_DIR"] = _tmp
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.replace(os.sep, '/')}/selfcheck.db"
os.environ.setdefault("OTP_SENDER", "console")


def main() -> int:
    from db.base import engine
    from otp.sender import OtpSender
    from verification import namematch, seed_demo, service
    from verification.models import ensure_schema

    class Recorder(OtpSender):
        channel = "sms"
        name = "recorder"

        def __init__(self):
            self.sent = []

        def send(self, destination, code, language):
            self.sent.append(code)

    failures = []

    def check(label, ok):
        print(f"  {'OK  ' if ok else 'FAIL'} {label}")
        if not ok:
            failures.append(label)

    ensure_schema(engine)
    seed_demo.seed()
    recorder = Recorder()
    svc = service.VerificationService(sender_resolver=lambda channel: recorder)

    check("name match ignores case, spacing and punctuation", namematch.names_match("  TEST  customer. ", namematch.normalize_name("Test Customer")))
    started = time.perf_counter()
    result = svc.lookup("Test Customer", "011 200 0001", "en", "127.0.0.1")
    check(f"lookup ({(time.perf_counter() - started) * 1000:.0f} ms)", [m["id"] for m in result.methods] == ["sms", "email"])
    check("methods are masked", result.methods[0]["masked"] == "07X XXX X123" and result.methods[1]["masked"].startswith("XXXXXXXXX@"))
    try:
        svc.lookup("Someone Else", "011 200 0001", "en", "127.0.0.1")
        check("wrong name refused", False)
    except service.VerifyError as error:
        check("wrong name refused with the generic answer", error.reason == "invalid_details")
    started = time.perf_counter()
    sent = svc.send_code(result.ticket, "sms", "en", "127.0.0.1")
    check(f"send ({(time.perf_counter() - started) * 1000:.0f} ms)", sent.expires_in == 120 and len(recorder.sent) == 1)
    started = time.perf_counter()
    done = svc.verify_code(sent.challenge_id, recorder.sent[-1], "127.0.0.1")
    check(f"verify ({(time.perf_counter() - started) * 1000:.0f} ms)", done.display_name == "Test Customer")
    try:
        svc.verify_code(sent.challenge_id, recorder.sent[-1], "127.0.0.1")
        check("a code works once", False)
    except service.VerifyError:
        check("a code works once", True)
    print("verification self check:", "FAILED" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
