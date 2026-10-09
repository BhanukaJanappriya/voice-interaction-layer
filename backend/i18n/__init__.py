"""Backend strings in English, Sinhala and Tamil.

    from i18n import t
    t("otp.sms_body", "si", code="123456", minutes=2)

Lookup order: the requested language, then English, then the key itself, so
a missing translation never raises and never produces an empty message.
Formatting never raises either: a placeholder with no value is left as is.

Files are read once and cached. Sinhala and Tamil are machine written and
flagged in each file's `_meta` as needing native speaker review.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DIR = Path(__file__).resolve().parent
DEFAULT_LANGUAGE = "en"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def available_languages() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.json"))


@lru_cache(maxsize=None)
def _load(lang: str) -> dict:
    path = _DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalise(lang: Optional[str]) -> str:
    short = (lang or DEFAULT_LANGUAGE).strip().lower()[:2]
    return short if short.isalpha() else DEFAULT_LANGUAGE


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


def _safe_format(template: str, values: dict) -> str:
    def replace(match):
        name = match.group(1)
        return str(values[name]) if name in values and values[name] is not None else match.group(0)

    return _PLACEHOLDER.sub(replace, template)


def t(key: str, lang: Optional[str] = None, **vars) -> str:
    for candidate in (_normalise(lang), DEFAULT_LANGUAGE):
        value = _load(candidate).get(key)
        if isinstance(value, str) and value:
            return _safe_format(value, vars)
    return key


def clear_cache() -> None:
    _load.cache_clear()
