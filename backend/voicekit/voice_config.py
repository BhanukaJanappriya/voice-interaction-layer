"""Reads config/voices.yaml: (tier, language, gender) to an ordered list of voice names.

    from voicekit.voice_config import lookup, gender_of
    lookup("local", "si", "male")            # ["si-LK-SameeraNeural"]
    lookup("local", "en", "female_natural")  # ["en-US-AriaNeural", "en-US-AvaMultilingualNeural"]
    gender_of("local", "en", "en-US-GuyNeural")  # "male"

Never raises. A missing, unreadable or half empty file falls back to DEFAULTS, and a
missing branch of a present file falls back to the same branch of DEFAULTS, so a typo
in the yaml cannot leave a language without a voice. null entries are dropped.
"""

import os
import threading
from typing import Dict, List, Optional

LANGUAGES = ("en", "si", "ta")
GENDERS = ("female", "male")
TIERS = ("gemini_live", "livekit_inference", "local")

DEFAULTS: Dict[str, dict] = {
    "gemini_live": {
        lang: {"female": ["Kore", "Aoede"], "male": ["Puck", "Charon"]} for lang in LANGUAGES
    },
    "livekit_inference": {
        "en": {"female": ["google/gemini-3.8-flash-lite-tts:Kore"], "male": ["google/gemini-3.8-flash-lite-tts:Puck"]},
        "si": {"female": ["inworld/inworld-tts-2:Ashley"], "male": ["inworld/inworld-tts-2:Edward"]},
        "ta": {"female": ["google/gemini-3.8-flash-tts:Kore"], "male": ["google/gemini-3.8-flash-tts:Puck"]},
    },
    "local": {
        "en": {"female": ["en-US-AriaNeural", "en-US-AvaMultilingualNeural"],
               "male": ["en-US-GuyNeural", "en-US-AndrewMultilingualNeural"]},
        "si": {"female": ["si-LK-ThiliniNeural"], "male": ["si-LK-SameeraNeural"]},
        "ta": {"female": ["ta-LK-SaranyaNeural", "ta-IN-PallaviNeural"],
               "male": ["ta-LK-KumarNeural", "ta-IN-ValluvarNeural"]},
    },
}

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "voices.yaml")

_lock = threading.Lock()
_cache: Dict[str, dict] = {}


def gender_from_style(voice_style: str) -> str:
    """female_natural | female | anything else -> 'female'; male -> 'male'."""
    return "male" if str(voice_style or "").strip().lower() == "male" else "female"


def _clean_list(value) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def load(path: Optional[str] = None, refresh: bool = False) -> dict:
    """Returns {tier: {lang: {gender: [names]}}}, merged over DEFAULTS."""
    path = os.path.abspath(path or os.environ.get("VOICES_CONFIG") or DEFAULT_PATH)
    with _lock:
        if path in _cache and not refresh:
            return _cache[path]
        raw: dict = {}
        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict) and isinstance(data.get("tiers"), dict):
                raw = data["tiers"]
        except Exception:
            raw = {}
        merged: Dict[str, dict] = {}
        for tier, langs in DEFAULTS.items():
            merged[tier] = {}
            for lang, genders in langs.items():
                merged[tier][lang] = {}
                for gender, default in genders.items():
                    try:
                        found = _clean_list(raw.get(tier, {}).get(lang, {}).get(gender))
                    except Exception:
                        found = []
                    merged[tier][lang][gender] = found or list(default)
        _cache[path] = merged
        return merged


def lookup(tier: str, language: str, voice_style: str = "female_natural", path: Optional[str] = None) -> List[str]:
    """Ordered voice names (first is preferred). Unknown language falls back to en,
    unknown tier to an empty list."""
    cfg = load(path)
    langs = cfg.get(tier)
    if not langs:
        return []
    by_gender = langs.get(language) or langs.get("en") or {}
    return list(by_gender.get(gender_from_style(voice_style), []))


def gender_of(tier: str, language: str, name: str, path: Optional[str] = None) -> Optional[str]:
    """'female' or 'male' for a configured voice name, None when it is not configured."""
    cfg = load(path)
    for lang in ([language] if language in cfg.get(tier, {}) else []) + list(LANGUAGES):
        by_gender = cfg.get(tier, {}).get(lang, {})
        for gender, names in by_gender.items():
            if name in names:
                return gender
    return None


def edge_map(path: Optional[str] = None) -> dict:
    """{(lang, 'female_natural'|'male'): first local voice}, the shape of voices.EDGE_VOICES."""
    return {
        (lang, style): (lookup("local", lang, style, path) or [""])[0]
        for lang in LANGUAGES
        for style in ("female_natural", "male")
    }
