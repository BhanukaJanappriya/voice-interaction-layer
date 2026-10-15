"""Default probe callables for VoiceRouter.

A probe returns normally when the tier looks usable and raises when it does
not; the router classifies the exception (reasons.classify). Probes never put
a key into an exception message. Heavy imports are lazy so importing this
module costs nothing.
"""

import concurrent.futures
from typing import Callable

from .types import Tier

GEMINI_PROBE_TIMEOUT_SECONDS = 8.0


class ProbeFailed(Exception):
    """Carries only a reason class name, which classify() understands."""


def gemini_live_probe() -> None:
    """A cheap models.list call, only when GEMINI_API_KEY is set."""
    import config

    key = getattr(config, "GEMINI_API_KEY", "")
    if not key:
        raise ProbeFailed("missing_key")

    def _call() -> None:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=int(GEMINI_PROBE_TIMEOUT_SECONDS * 1000)),
        )
        for _ in client.models.list(config={"page_size": 1}):
            break

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_call)
    try:
        future.result(timeout=GEMINI_PROBE_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        raise ProbeFailed("timeout") from None
    except Exception as exc:  # noqa: BLE001
        # Re-raise so classify() sees the status code, but with a clean message
        # (SDK errors can echo request details).
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        wrapped = ProbeFailed(type(exc).__name__)
        wrapped.code = code if isinstance(code, int) else None  # type: ignore[attr-defined]
        raise wrapped from None
    finally:
        pool.shutdown(wait=False)


def livekit_probe() -> None:
    """Credentials present and a token can be minted. No network."""
    import config

    if not config.livekit_configured():
        raise ProbeFailed("missing_key")
    try:
        from livekit import api
    except ImportError:
        return  # the SDK is optional here; configured is the best check available
    try:
        token = api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET).with_identity("probe").to_jwt()
    except Exception:  # noqa: BLE001
        raise ProbeFailed("invalid_key") from None
    if not token:
        raise ProbeFailed("invalid_key")


def local_probe() -> None:
    """Ollama /api/tags, like llm_chain's LocalTier.probe."""
    import config
    import requests

    try:
        response = requests.get(f"{config.LOCAL_LLM_HOST.rstrip('/')}/api/tags", timeout=3)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        code = getattr(getattr(exc, "response", None), "status_code", None)
        wrapped = ProbeFailed(type(exc).__name__)
        wrapped.code = code if isinstance(code, int) else None  # type: ignore[attr-defined]
        raise wrapped from None


def default_probes() -> "dict[Tier, Callable[[], None]]":
    return {Tier.GEMINI_LIVE: gemini_live_probe, Tier.LIVEKIT: livekit_probe, Tier.LOCAL: local_probe}
