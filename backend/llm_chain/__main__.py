"""Standalone self check: python -m llm_chain (run from the backend folder).

Uses fake tiers only, so it needs no key, no network and no Ollama. It walks
the chain through its main situations and prints how long each tier took to
produce its first text, which is the number that matters for a voice reply.
"""

import time
from typing import Iterator

from .chain import LlmChain
from .errors import SafetyBlocked, TierUnavailable
from .tiers import Tier, Turn


class _FakeTier(Tier):
    def __init__(self, name: str, label: str, behavior: str = "ok", delay: float = 0.0):
        self.name = name
        self._label = label
        self.behavior = behavior
        self.delay = delay

    def label(self) -> str:
        return self._label

    def configured(self) -> bool:
        return True

    def _open_stream(self, turn: Turn) -> Iterator[str]:
        time.sleep(self.delay)
        if self.behavior == "down":
            raise TierUnavailable("outage")
        if self.behavior == "safety":
            raise SafetyBlocked()
        yield f"Answer from {self.name}. "
        yield "Done."


def _run(title: str, chain: LlmChain, text: str = "What is two plus two?") -> None:
    start = time.perf_counter()
    reply = chain.respond(Turn(text=text))
    total = time.perf_counter() - start
    print(f"{title}: mode={chain.last_mode!r} total={total:.2f}s reply={reply!r}")


def main() -> int:
    def build(gemini: str, livekit: str = "ok") -> LlmChain:
        tiers = {
            "gemini": _FakeTier("gemini", "cloud (gemini)", gemini, delay=0.05),
            "livekit": _FakeTier("livekit", "livekit inference (fake)", livekit, delay=0.08),
            "local": _FakeTier("local", "local single agent (Llama via Ollama)", delay=0.2),
        }
        return LlmChain(order=["gemini", "livekit", "local"], tiers=tiers, breaker_seconds=300)

    _run("all healthy", build("ok"))
    _run("gemini down", build("down"))
    _run("gemini and livekit down", build("down", "down"))
    _run("gemini safety block", build("safety"))

    chain = build("down")
    _run("first turn, gemini trips", chain)
    _run("second turn, gemini skipped", chain)
    status = chain.status()
    for name, info in status["tiers"].items():
        print(f"  {name}: state={info['state']} retry_in={info['retry_in']}s last_error={info['last_error']!r}")
    print("Per tier first text latency (seconds):", {k: round(v, 2) for k, v in chain.latencies.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
