"""Self check: python -m access   (run from the backend directory).

Runs the guard over a sample set, checks the expected outcome of each
sample, and prints the added latency of the guard.
"""

import statistics
import sys
import time

from . import messages
from .context import GUEST, AccessContext
from .guard import evaluate
from .policy import get_policy
from .tool_gate import AccessDenied, authorize_tool

# (text, expected allowed for a guest)
SAMPLES = [
    ("What is my bill this month?", False),
    ("how much data balance do I have left", False),
    ("my internet is not working", False),
    ("I want to log a fault", False),
    ("please change my package to the unlimited one", False),
    ("show my daily usage", False),
    ("මගේ බිල කීයද?", False),
    ("ඉතිරි දත්ත කීයද", False),
    ("අන්තර්ජාලය වැඩ කරන්නේ නැහැ", False),
    ("mage bill eka kiyada", False),
    ("net eka wada naha", False),
    ("எனது பில் எவ்வளவு", False),
    ("இணைப்பு நிலை என்ன", False),
    ("en balance evvalavu irukku", False),
    ("why was I charged extra this month", False),
    ("How can I pay my bill online?", True),
    ("What packages do you offer for fibre?", True),
    ("What is the hotline number?", True),
    ("Tell me a joke", True),
    ("What is the capital of Sri Lanka?", True),
    ("ඔන්ලයින් බිල්පත ගෙවන්නේ කොහොමද?", True),
    ("hello, how are you today", True),
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    policy = get_policy()
    print(f"policy: {policy.source}, {len(policy.topics)} topics, threshold {policy.threshold}")
    failures = 0
    for text, expected in SAMPLES:
        decision = evaluate(text, None, GUEST)
        ok = decision.allowed == expected
        failures += 0 if ok else 1
        mark = "ok  " if ok else "FAIL"
        verdict = "allow" if decision.allowed else "block"
        print(f"{mark} {verdict:5} {decision.source:10} {str(decision.topic):18} {text}")
    verified = AccessContext.verified(1, "Test", "+94112345678")
    if not evaluate("what is my bill", None, verified).allowed:
        print("FAIL verified user was blocked")
        failures += 1
    try:
        authorize_tool("show_gb_balance", {"telephone_no": "0112345678"}, GUEST)
        print("FAIL guest tool call was allowed")
        failures += 1
    except AccessDenied:
        pass
    print("blocked reply (en):", messages.blocked_reply("en"))

    timings = []
    texts = [t for t, _ in SAMPLES]
    for _ in range(300):
        for text in texts:
            started = time.perf_counter()
            evaluate(text, None, GUEST)
            timings.append((time.perf_counter() - started) * 1000.0)
    timings.sort()
    p50 = statistics.median(timings)
    p95 = timings[int(len(timings) * 0.95)]
    print(f"guard latency over {len(timings)} calls: mean {statistics.fmean(timings):.3f} ms, "
          f"p50 {p50:.3f} ms, p95 {p95:.3f} ms, max {timings[-1]:.3f} ms (target well under 5 ms)")
    print("RESULT:", "all samples as expected" if failures == 0 else f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
