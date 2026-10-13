"""Which account topics a verified customer's message is about.

The topic rules are the access policy's own (backend/access/policy.json): the
same normalization, the same public overrides, the same keyword matchers and
regular expressions as the guard, in English, Sinhala, Tamil and the Latin
letter forms. The heuristic classifier is the last resort and maps to "account".
This module adds only one thing the policy does not have: detection of a
request to CHANGE something (buy, activate, subscribe, cancel, add, upgrade).
"""

import re
from dataclasses import dataclass

from access import classifier
from access.policy import get_policy, normalize

from .tools import TOPICS


@dataclass(frozen=True)
class Intent:
    topics: tuple = ()
    changes: bool = False


_LATIN = (
    r"(?<![a-z0-9])("
    r"buy|bought|purchase|activate|deactivate|subscribe|unsubscribe|cancel|terminate|disconnect|upgrade|downgrade|"
    r"add|top ?up|recharge|"
    r"(change|switch|migrate|move)\b[^?.!]{0,30}\b(package|plan|bundle|number|speed|service|connection)|"
    r"(log|report|raise|register|lodge) (a |an |my |the )?(fault|complaint|ticket|problem)|"
    # Latin letter Sinhala and Tamil
    r"(activate|cancel|add|upgrade|subscribe|purchase|buy|recharge|wenas|maru|deactivate) (karanna|kara|karala|pannu|pannunga|panni|pannanum)|"
    r"ganna (ona|one|puluwan)|gannako|mila(dee|di) ganna|wenas karanna|maru karanna|"
    r"vaanga|vanga|vaangi|maathu|maatha|maattu|ratthu|rathu|sertha|serkka"
    r")(?![a-z0-9])"
)

_SCRIPT = (
    # Sinhala
    "මිලදී ගන්න", "මිලදී ගැනී", "මිලදී ගත", "සක්‍රීය කර", "සක්‍රිය කර", "එකතු කර", "අවලංගු", "උසස් කර",
    "අප්ග්‍රේඩ්", "ඇක්ටිවේට්", "සබ්ස්ක්‍රයිබ්", "කැන්සල්", "වෙනස් කර", "මාරු කර", "ඉවත් කර", "රීචාර්ජ්",
    # Tamil
    "வாங்க", "வாங்கு", "செயல்படுத்த", "செயல்படுத்து", "சேர்க்க", "சேர்த்து", "ரத்து", "நீக்க", "மேம்படுத்த",
    "மாற்ற", "மாற்று", "ஆக்டிவேட்", "கேன்சல்", "சப்ஸ்கிரைப்", "ரீசார்ஜ்",
)

_CHANGE_RX = re.compile(
    _LATIN + "|" + "|".join(re.escape(normalize(word)) for word in _SCRIPT)
)


def wants_change(text: str) -> bool:
    return bool(_CHANGE_RX.search(normalize(text)))


def detect(text: str, language, access=None) -> Intent:
    """Topics (policy ids this package has tools for) and the changes flag.
    The declared language is not used for matching on purpose (people mix
    scripts), like the guard."""
    if not isinstance(text, str) or not text.strip():
        return Intent()
    policy = get_policy()
    rest = normalize(text)
    for override in policy.overrides:
        rest = override.sub(" ", rest)
    found: list = []
    for topic in policy.topics:
        hit = topic.matcher is not None and topic.matcher.search(rest)
        hit = hit or any(rx.search(rest) for rx in topic.regexes)
        if hit and topic.id in TOPICS and topic.id not in found:
            found.append(topic.id)
    changes = wants_change(text)
    if not found:
        try:
            if classifier.classify(rest, policy.threshold).needs_customer_data:
                found.append("account")
        except Exception:  # noqa: BLE001 - a classifier problem only means no extra topic
            pass
    if not found and changes:
        found.append("package_change")
    return Intent(tuple(found), changes)
