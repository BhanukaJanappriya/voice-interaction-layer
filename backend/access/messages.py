"""The canned reply for a blocked guest request.

Text comes from the i18n key access.customer_service_blocked when the
backend/i18n package exists and returns a text that contains the official
numbers. Otherwise a built in text is used. The numbers always come from the
policy dial section, so a supervisor edit changes the reply.

The Sinhala and Tamil built in texts are machine written and need a review
by a native speaker.
"""

from typing import Optional

from .policy import PolicyError, get_policy

KEY = "access.customer_service_blocked"

_DEFAULT_DIAL = {"hotline": "1212", "sms": "0113121212", "prepaid": "1222"}

_BUILTIN = {
    "en": (
        "I am sorry, I cannot help with account specific matters such as bills, balances, "
        "connection status, faults or package changes until you are verified. You can verify with a "
        "one time code, or contact SLT directly: call the hotline {hotline} (toll free from SLT lines), "
        "send an SMS to {sms}, or call {prepaid} for CDMA prepaid. I am happy to answer general "
        "questions in the meantime."
    ),
    "si": (
        "කණගාටුයි, ඔබව තහවුරු කරන තුරු බිල්පත්, ශේෂය, සම්බන්ධතා තත්ත්වය, දෝෂ හෝ පැකේජ වෙනස් කිරීම් වැනි "
        "ගිණුමට අදාළ කරුණු සඳහා මට උදව් කළ නොහැක. ඔබට එක් වරක් භාවිත කරන කේතයකින් තහවුරු විය හැක, නැතහොත් "
        "SLT වෙත කෙළින්ම අමතන්න: හොට්ලයින් {hotline} (SLT ඇමතුම් වලින් නොමිලේ), SMS {sms}, "
        "CDMA ප්‍රීපේඩ් සඳහා {prepaid}. මේ අතරතුර සාමාන්‍ය ප්‍රශ්නවලට පිළිතුරු දීමට මම සතුටු වෙමි."
    ),
    "ta": (
        "மன்னிக்கவும், நீங்கள் சரிபார்க்கப்படும் வரை கட்டணப் பட்டியல், இருப்பு, இணைப்பு நிலை, பழுதுகள் அல்லது "
        "பேக்கேஜ் மாற்றங்கள் போன்ற கணக்கு தொடர்பான விஷயங்களில் என்னால் உதவ முடியாது. ஒருமுறை பயன்படுத்தும் "
        "குறியீட்டால் சரிபார்க்கலாம், அல்லது SLT-ஐ நேரடியாக தொடர்பு கொள்ளுங்கள்: ஹாட்லைன் {hotline} "
        "(SLT இணைப்புகளிலிருந்து இலவசம்), SMS {sms}, CDMA ப்ரீபெய்டுக்கு {prepaid}. "
        "அதுவரை பொதுவான கேள்விகளுக்கு பதிலளிக்க மகிழ்ச்சி."
    ),
}


def _dial() -> dict:
    try:
        dial = get_policy().dial
        return {k: dial.get(k) or _DEFAULT_DIAL[k] for k in _DEFAULT_DIAL}
    except PolicyError:
        return dict(_DEFAULT_DIAL)


def _from_i18n(language: str, dial: dict) -> Optional[str]:
    try:
        from i18n import t  # lazy: backend/i18n is built by another workstream
    except Exception:  # noqa: BLE001
        return None
    try:
        text = t(KEY, language, **dial)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(text, str) or not text or text == KEY:
        return None
    if not all(number in text for number in dial.values()):
        return None  # a translation without the official numbers is not safe to use
    return text


def blocked_reply(language: Optional[str] = None) -> str:
    lang = (language or "en").strip().lower()[:2]
    if lang not in _BUILTIN:
        lang = "en"
    dial = _dial()
    return _from_i18n(lang, dial) or _BUILTIN[lang].format(**dial)
