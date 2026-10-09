"""The supervisor editable policy (policy.json) and its loader.

The file is reloaded when its modified time or size changes. A bad file
keeps the last good policy; the very first load of a bad file raises
PolicyError. Matching is done on text normalized by normalize().
"""

import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).with_name("policy.json")
_INVISIBLE = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF])
_SPACE = re.compile(r"\s+")


class PolicyError(Exception):
    """The policy file is missing, unreadable or malformed."""


def normalize(text: str) -> str:
    """NFC, casefold, collapse whitespace, drop zero width joiners (Sinhala
    and Tamil text is typed with and without them)."""
    text = unicodedata.normalize("NFC", text or "").translate(_INVISIBLE)
    return _SPACE.sub(" ", text.casefold()).strip()


def _keyword_pattern(keyword: str) -> str:
    kw = normalize(keyword)
    prefix_wild = kw.endswith("*")
    kw = kw.rstrip("*")
    if not kw:
        raise PolicyError("empty keyword")
    pattern = re.escape(kw).replace("\\ ", " ")
    if kw[0].isascii() and kw[0].isalnum():
        pattern = r"(?<![a-z0-9])" + pattern
        if not prefix_wild and kw[-1].isascii() and kw[-1].isalnum():
            pattern += r"(?![a-z0-9])"
    return pattern


@dataclass(frozen=True)
class Topic:
    id: str
    matcher: Optional["re.Pattern[str]"]
    regexes: tuple


@dataclass(frozen=True)
class Policy:
    topics: tuple
    overrides: tuple
    dial: dict
    threshold: float
    public_tools: frozenset
    customer_tools: frozenset
    source: str = ""


def _compile(pattern: str) -> "re.Pattern[str]":
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise PolicyError(f"bad regular expression {pattern!r}: {exc}") from exc


def parse_policy(data: dict, source: str = "") -> Policy:
    try:
        topics = []
        for raw in data["topics"]:
            patterns = []
            for words in (raw.get("keywords") or {}).values():
                patterns.extend(_keyword_pattern(w) for w in words)
            # Longest first so the alternation prefers the most specific phrase.
            patterns.sort(key=len, reverse=True)
            matcher = _compile("|".join(patterns)) if patterns else None
            regexes = tuple(_compile(r) for r in raw.get("regex") or [])
            topics.append(Topic(str(raw["id"]), matcher, regexes))
        overrides = tuple(_compile(r) for r in data.get("public_overrides") or [])
        dial = {k: str(v) for k, v in (data.get("dial") or {}).items()}
        for key in ("hotline", "sms", "prepaid"):
            if not dial.get(key):
                raise PolicyError(f"dial.{key} is missing")
        threshold = float((data.get("classifier") or {}).get("threshold", 0.53))
        if not 0.0 < threshold <= 1.0:
            raise PolicyError("classifier.threshold must be in (0, 1]")
        public = frozenset(str(n) for n in data.get("public_tools") or [])
        customer = frozenset(str(n) for n in data.get("customer_tools") or [])
    except PolicyError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PolicyError(f"malformed policy: {exc!r}") from exc
    return Policy(tuple(topics), overrides, dial, threshold, public, customer, source)


def load_policy_file(path: Path) -> Policy:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError("policy root must be an object")
    return parse_policy(data, str(path))


@dataclass
class _Store:
    path: Optional[Path] = None
    policy: Optional[Policy] = None
    stamp: tuple = field(default_factory=tuple)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_error: Optional[str] = None


_store = _Store()


def _configured_path() -> Path:
    if _store.path is not None:
        return _store.path
    raw = os.environ.get("ACCESS_POLICY_PATH", "").strip()
    if not raw:
        try:
            import config  # lazy: this package must stay importable alone

            raw = (getattr(config, "ACCESS_POLICY_PATH", "") or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    return Path(raw) if raw else DEFAULT_PATH


def set_policy_path(path: Optional[os.PathLike]) -> None:
    """Point the loader at another file (tests, tools). None restores the
    configured default."""
    with _store.lock:
        _store.path = Path(path) if path is not None else None
        _store.policy = None
        _store.stamp = ()
        _store.last_error = None


def get_policy() -> Policy:
    """The current policy, reloading when the file changed. A bad edit keeps
    the last good policy (see last_error())."""
    path = _configured_path()
    try:
        st = path.stat()
        stamp = (str(path), st.st_mtime_ns, st.st_size)
    except OSError as exc:
        if _store.policy is not None:
            _store.last_error = f"cannot stat {path}: {exc}"
            return _store.policy
        raise PolicyError(f"cannot read {path}: {exc}") from exc
    if _store.policy is not None and stamp == _store.stamp:
        return _store.policy
    with _store.lock:
        if _store.policy is not None and stamp == _store.stamp:
            return _store.policy
        try:
            _store.policy = load_policy_file(path)
            _store.last_error = None
        except PolicyError as exc:
            if _store.policy is None:
                raise
            _store.last_error = str(exc)
            print(f"Access policy reload failed, keeping the last good policy: {exc}")
        _store.stamp = stamp  # do not retry a bad file until it changes again
        return _store.policy


def last_error() -> Optional[str]:
    return _store.last_error
