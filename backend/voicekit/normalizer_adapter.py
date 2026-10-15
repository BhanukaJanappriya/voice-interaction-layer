"""The one function the server calls before synthesis.

    from voicekit.normalizer_adapter import prepare_for_tts
    spoken = prepare_for_tts(reply_text, language, tier="local")

Tier 1 (gemini_live) speaks natively and is left alone. Setting VOICE_NORMALIZE=0 turns the
normalizer off everywhere (a way to compare by ear). Never raises; on any problem the text
comes back unchanged.
"""

import os

NATIVE_TIERS = ("gemini_live",)


def enabled() -> bool:
    return os.environ.get("VOICE_NORMALIZE", "1").strip().lower() not in ("0", "false", "no", "off")


def prepare_for_tts(text: str, language: str, tier: str = "local") -> str:
    if not isinstance(text, str) or not text or tier in NATIVE_TIERS or not enabled():
        return text if isinstance(text, str) else ""
    try:
        try:
            from voice_text import normalize
        except ImportError:  # imported as backend.voicekit...
            from backend.voice_text import normalize  # type: ignore
        return normalize(text, language)
    except Exception:
        return text
