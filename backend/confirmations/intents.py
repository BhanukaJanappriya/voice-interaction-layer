"""Reading the customer's words: a clear yes, a no, and what they want to do.

A clear yes is deliberately narrow. The whole sentence must be made of yes
words and short polite fillers; a question mark, a negation, or any other word
makes it NOT a yes. Everything that is not a clear yes cancels a waiting
action, so a wrong reading can only ever do nothing, never buy something.
"""

import re
import unicodedata

# Words that say yes. STRONG ones can stand alone; WEAK ones only help a
# strong one ("go ahead", "do it", "ow karanna").
STRONG_YES = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "confirm", "confirmed", "sure", "proceed", "ya",
    "ow", "owu", "hari", "ඔව්", "හරි", "තහවුරු", "ස්ථිරයි",
    "ஆம்", "சரி", "உறுதி", "ஓம்", "seri", "aam",
}
WEAK_YES = {"go", "ahead", "do", "it", "karanna", "කරන්න", "செய்யுங்கள்", "செய்", "pannunga"}
FILLERS = {
    "please", "pls", "plz", "thanks", "thank", "you", "now", "kindly", "then", "thanx",
    "ස්තුතියි", "කරුණාකර", "நன்றி", "தயவுசெய்து", "istuti", "karunakara",
}
NO_WORDS = {
    "no", "nope", "nah", "cancel", "stop", "dont", "donot", "never", "mind", "nevermind", "not",
    "නැහැ", "නෑ", "එපා", "අවලංගු", "nahe", "naha", "epa", "ne", "na",
    "வேண்டாம்", "இல்லை", "ரத்து", "venda", "illai",
}

SKIP_WORDS = {
    "skip", "none", "nothing", "no", "nope", "later", "මඟ", "මග", "හරින්න", "නැහැ", "නෑ",
    "தவிர்", "இல்லை", "வேண்டாம்", "skipp", "epa", "එපා",
}


def normalise(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).lower().strip()


def tokens(text: str) -> "list[str]":
    """Words with punctuation and symbols removed. Combining marks stay, so
    Sinhala and Tamil words are not split."""
    cleaned = []
    for ch in normalise(text):
        if ch in "'’":
            continue
        if unicodedata.category(ch)[0] in "PS":
            cleaned.append(" ")
        else:
            cleaned.append(ch)
    return "".join(cleaned).split()


def classify_reply(text: str) -> str:
    """"yes" only for a clear yes, "no" for a clear no, else "other"."""
    raw = str(text or "")
    if "?" in raw or "？" in raw or len(raw) > 80:
        return "other"
    words = tokens(raw)
    if not words or len(words) > 8:
        return "other"
    if any(w in NO_WORDS for w in words):
        return "no" if all(w in NO_WORDS or w in FILLERS for w in words) else "other"
    pairs = {(a, b) for a, b in zip(words, words[1:])}
    if not any(w in STRONG_YES for w in words) and not ({("go", "ahead"), ("do", "it")} & pairs):
        return "other"
    if all(w in STRONG_YES or w in WEAK_YES or w in FILLERS for w in words):
        return "yes"
    return "other"


def is_skip(text: str) -> bool:
    words = tokens(text)
    return bool(words) and len(words) <= 3 and all(w in SKIP_WORDS or w in FILLERS for w in words)


def wants_cancel(text: str) -> bool:
    words = tokens(text)
    return bool(words) and len(words) <= 4 and any(w in ("cancel", "stop", "අවලංගු", "ரத்து", "epa", "එපා", "நிறுத்து") for w in words)


# What the customer wants to do

_LOG_VERBS = re.compile(
    r"\b(log|report|raise|lodge|register|create|open|submit|make|file|karanna|karan|ekak)\b"
    r"|වාර්තා|ලොග්|ඉදිරිපත්|පෙන්වන්න|பதிவு|செய்ய|பதிவிட"
)
_FAULT_TOPIC = re.compile(
    r"\b(fault|faults|complaint|complain)\b|දෝෂ|පැමිණිලි|පැමිනිල්|පැමිණිල්ල|பழுது|புகார்|\bkopplainta\b|\bfault eka\b"
)
_BUY_VERBS = re.compile(
    r"\b(buy|purchase|get|add|want|need|order|subscribe|activate|top ?up|recharge|ganna|gannawa|ganin|ona|oney|ekathu)\b"
    r"|ගන්න|ගනින්|මිලදී|ඕන|ඕනේ|අවශ්‍ය|එකතු|ලබා|வாங்க|வாங்கு|வேண்டும்|சேர்க்க|சேர்|செயல்படுத்த|வாங்கி"
)
_STRONG_BUY = re.compile(r"(buy|purchase|ganna|gannawa|ganin|order)|මිලදී|ගන්න|வாங்க|வாங்கு|வாங்கி")
_INFO = re.compile(
    r"balance|usage|used|remaining|left|how much|check|bill|history|ඉතිරි|ශේෂය|භාවිත|பயன்பாடு|இருப்பு|மீதம்|மீதி"
)
_VAS_TOPIC = re.compile(r"\bvas\b|bundle|බණ්ඩ|වාස්|பண்டில்|வாஸ்")
_DATA_TOPIC = re.compile(
    r"\b(gb|data|extra|additional|add.?on|addon|amatara)\b|\d\s*gb|අමතර|දත්ත|ඩේටා|ජීබී|ஜிபி|டேட்டா|கூடுதல்|எக்ஸ்ட்ரா"
)
_PACKAGE_TOPIC = re.compile(r"\b(package|pack|plan)\b|පැකේජ|පැකේජය|பேக்கேஜ்|பேக்")


def detect_request(text: str) -> str:
    """What the words ask for: "fault", "vas", "data" (extra GB or a data
    package) or "package" (any package, only meaningful for LTE prepaid); "" for none."""
    low = normalise(text)
    if not low:
        return ""
    if _FAULT_TOPIC.search(low) and _LOG_VERBS.search(low):
        return "fault"
    if re.search(r"புகார்|පැමිණිලි", low):
        return "fault"
    if not _BUY_VERBS.search(low):
        return ""
    if _INFO.search(low) and not _STRONG_BUY.search(low):
        return ""
    if _VAS_TOPIC.search(low):
        return "vas"
    if _DATA_TOPIC.search(low):
        return "data"
    if _PACKAGE_TOPIC.search(low):
        return "package"
    return ""


# Fault service choice

_OPTIONS = (
    ("PEOTV", re.compile(r"\bpeo\b|peotv|\btv\b|television|ටීවී|ටිවී|தொலைக்காட்சி|டிவி")),
    ("BB", re.compile(r"broadband|\bbb\b|internet|fibre|fiber|adsl|wifi|wi-fi|data|බ්‍රෝඩ්බෑන්ඩ්|අන්තර්ජාල|ඉන්ටර්නෙට්|இணையம்|பிராட்பேண்ட்|இன்டர்நெட்")),
    ("VOICE", re.compile(r"voice|phone|telephone|landline|call|හඬ|දුරකථන|ෆෝන්|குரல்|தொலைபேசி|ஃபோன்")),
    ("OTHER", re.compile(r"other|else|වෙනත්|වෙන|வேறு")),
)


def fault_option(text: str) -> str:
    low = normalise(text)
    for name, pattern in _OPTIONS:
        if pattern.search(low):
            return name
    return ""
