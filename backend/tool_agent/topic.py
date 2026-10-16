"""Is this question about SLT or telecom services at all?

Used only when the SLT knowledge base found nothing relevant. A question that is about SLT, a
package, a bill, a line, a connection and the like must be answered from the hub or with the fixed
"call 1212" text, never from the model's memory. A question that has nothing to do with telecom
(a capital city, a recipe, a maths question) can be answered politely from general knowledge, the
way a friendly customer care officer would.

When unsure the answer is True: that gives the fixed hub only reply, which is the safe side. English,
Sinhala, Tamil and the Latin letter forms are covered by whole word and substring rules below.
"""

import re

from access.policy import normalize

# Latin letter words (matched at a word start and end after normalization)
_EN = (
    r"slt|mobitel|sltmobitel|peo|peotv|peo ?tv|fib(?:re|er)|ftth|broadband|adsl|vdsl|lte|4g|5g|3g|wi ?fi|wifi|router|modem|"
    r"mbps|gbps|internet|data (?:package|plan|pack|bundle|usage|balance|card|add ?on)|mobile data|add ?on|combo|bundle|"
    r"package|packages|pack|packs|tariff|tariffs|roaming|idd|sms|recharge|reload|top ?up|prepaid|postpaid|"
    r"bill|bills|billing|invoice|payment|account|my balance|account balance|credit balance|remaining balance|talk ?time|connection|new connection|disconnect|reconnect|"
    r"sim|simcard|sim card|landline|telephone (?:number|line|bill|connection|service)|phone number|mobile number|extension|hotline|customer care|call cent(?:er|re)|"
    r"teleshop|tele shop|regional telecom|signal|coverage|outage|fault|complaint|line|"
    r"channel|channels|set ?top|settop|my ?slt|mysl|youtube|facebook|whatsapp|messenger|netflix|"
    r"1212|1222|activate|deactivate|subscribe|unsubscribe|relocat\w*|installation|"
    r"rental|discount|promotion|promo|voucher|loyalty|vas|ussd|"
    r"singer|mega|big m|touch point"
)
# Sinhala and Tamil script words, matched as substrings (those languages attach endings to words)
_SI_TA = (
    "ෆයිබර්|ෆයිබර|බ්‍රෝඩ්බෑන්ඩ්|ඉන්ටර්නෙට්|අන්තර්ජාල|පැකේජ|පැකෙජ|දත්ත|ඩේටා|බිල|ගෙවීම|ගෙවන්න|ගිණුම|සම්බන්ධතා|දුරකථන|"
    "ෆෝන්|රවුටර්|වයිෆයි|රිචාජ්|රීලෝඩ්|ප්‍රීපේඩ්|පෝස්ට්පේඩ්|රෝමිං|වේගය|ගාස්තු|සිග්නල්|ආවරණ|මොබිටෙල්|පියෝ|ටීවී|"
    "එල්ටීඊ|සිම්|ඇමතුම්|කෙටි පණිවිඩ|දෝෂ|පැමිණිලි|හොට්ලයින්|"
    "ஃபைபர்|ஃபைபர|பைபர்|பிராட்பேண்ட்|இணைய|இன்டர்நெட்|பேக்கேஜ்|தொகுப்பு|டேட்டா|தரவு|பில்|கட்டண|கணக்கு|இணைப்பு|தொலைபேசி|"
    "ரூட்டர்|வைஃபை|ரீசார்ஜ்|ரீலோட்|ப்ரீபெய்ட்|போஸ்ட்பெய்ட்|ரோமிங்|வேகம்|சிக்னல்|கவரேஜ்|மொபிடெல்|பியோ|டிவி|எல்டிஇ|"
    "சிம்|அழைப்பு|குறுஞ்செய்தி|பழுது|புகார்|ஹாட்லைன்"
)
# Latin letter Sinhala (Singlish) and Tamil (Tanglish) topic words
_ROMAN = (
    r"fibre eka|data eka|package eka|bill eka|connection eka|router eka|recharge karanna|reload karanna|"
    r"balance eka|salli|gewanna|gewana|geweema|bila|bil eka|internet eka|wifi eka|signal eka|"
    r"pothu|kattanam|kattana|pill|paisa"
)

_LATIN = re.compile(r"(?<![a-z0-9])(?:" + _EN + r"|" + _ROMAN + r")(?![a-z0-9])")
_SCRIPT = re.compile("(?:" + _SI_TA + ")")


# "What is fibre?", "how does roaming work", "difference between 4G and fibre": a question about an idea,
# not about what SLT sells or charges. Matched at the start of the question (English only; the Sinhala and
# Tamil questions of this kind are left to the hub, which is the safe side).
_CONCEPT = re.compile(
    r"^(?:what(?: is|s| are|'s)(?: a| an| the)?|explain|define|meaning of|how (?:does|do|is|are) .{1,40}? (?:work|works|different)|"
    r"difference between|what does .{1,30}? mean|why (?:is|do|does|are)|tell me about the (?:idea|concept) of)\b"
)
_ABOUT_SLT = re.compile(r"(?<![a-z0-9])(?:slt|mobitel|sltmobitel|peo|peotv|my slt|myslt|your|you|do you|can i|my|price|cost|rs|lkr|charge|rental|package for)(?![a-z0-9])")


def is_concept_question(text: str) -> bool:
    """A question about what something is or how it works, not about SLT's own offers, prices or accounts."""
    if not isinstance(text, str):
        return False
    rest = normalize(text)
    return bool(_CONCEPT.search(rest)) and not _ABOUT_SLT.search(rest)


def is_slt_topic(text: str) -> bool:
    """True when the question is, or might be, about SLT or a telecom service."""
    if not isinstance(text, str):
        return True
    rest = normalize(text)
    if not rest:
        return True
    return bool(_LATIN.search(rest) or _SCRIPT.search(rest))
