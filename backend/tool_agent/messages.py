"""Texts of the grounded answering layer.

text(key, language) reads the i18n key and falls back to the built in English,
Sinhala or Tamil text, so the layer works before the i18n files are merged. The
official numbers always come from the access policy dial section. The Sinhala
and Tamil texts are machine written and need a native review. Clauses
(instructions to the model) are English on purpose, like the rest of the
project, and ask for the reply in the customer's language.
"""

from typing import Optional

from customer_service.messages import _dial

LANGUAGE_NAMES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}

BUILTIN = {
    "tool_agent.not_found": {
        "en": "I could not find that in the SLT information I have. Please call {hotline} (toll free from SLT lines), "
              "send an SMS to {sms}, or call {prepaid} for prepaid.",
        "si": "මා සතුව ඇති SLT තොරතුරු අතර එය සොයාගත නොහැකි විය. කරුණාකර {hotline} (SLT දුරකථනවලින් නොමිලේ) අමතන්න, "
              "{sms} වෙත SMS එවන්න, නැතහොත් පෙර ගෙවුම් සඳහා {prepaid} අමතන්න.",
        "ta": "என்னிடம் உள்ள SLT தகவல்களில் அதைக் கண்டுபிடிக்க முடியவில்லை. தயவுசெய்து {hotline} (SLT இணைப்புகளிலிருந்து "
              "கட்டணமில்லை) அழைக்கவும், {sms} எண்ணுக்கு SMS அனுப்பவும், அல்லது முன்கட்டணச் சேவைக்கு {prepaid} அழைக்கவும்.",
    },
    "tool_agent.hub_unavailable": {
        "en": "The SLT information service is temporarily unavailable. Please try again shortly, or contact SLT: "
              "call {hotline} (toll free from SLT lines), send an SMS to {sms}, or call {prepaid} for prepaid.",
        "si": "SLT තොරතුරු සේවාව තාවකාලිකව ලබා ගත නොහැක. කරුණාකර මදක් පසුව නැවත උත්සාහ කරන්න, නැතහොත් SLT අමතන්න: "
              "{hotline} (SLT දුරකථනවලින් නොමිලේ), {sms} වෙත SMS එවන්න, නැතහොත් පෙර ගෙවුම් සඳහා {prepaid} අමතන්න.",
        "ta": "SLT தகவல் சேவை தற்காலிகமாகக் கிடைக்கவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயலவும், அல்லது SLT-ஐ "
              "தொடர்பு கொள்ளுங்கள்: {hotline} (SLT இணைப்புகளிலிருந்து கட்டணமில்லை), {sms} எண்ணுக்கு SMS, "
              "முன்கட்டணச் சேவைக்கு {prepaid}.",
    },
    "tool_agent.session_expired": {
        "en": "Your verified session has ended, so I can no longer open your account details. "
              "Please verify again with a one time code and then ask me again.",
        "si": "ඔබගේ තහවුරු කළ සැසිය අවසන් වී ඇත, එබැවින් ඔබගේ ගිණුම් විස්තර දැන් විවෘත කළ නොහැක. "
              "කරුණාකර එක් වරක් භාවිත කරන කේතයකින් නැවත තහවුරු වී, ඉන්පසු නැවත මගෙන් අසන්න.",
        "ta": "உங்கள் சரிபார்க்கப்பட்ட அமர்வு முடிந்துவிட்டது, அதனால் உங்கள் கணக்கு விவரங்களை இப்போது திறக்க முடியாது. "
              "ஒருமுறை பயன்படுத்தும் குறியீட்டால் மீண்டும் சரிபார்த்து, பிறகு என்னிடம் கேளுங்கள்.",
    },
    "tool_agent.more_offer": {
        "en": "There are {count} more. Would you like me to read them?",
        "si": "තවත් {count} ක් ඇත. ඒවා කියවන්නද?",
        "ta": "இன்னும் {count} உள்ளன. அவற்றைப் படிக்கவா?",
    },
    "tool_agent.status.search": {
        "en": "Searching the SLT information",
        "si": "SLT තොරතුරු සොයමින්",
        "ta": "SLT தகவல்களைத் தேடுகிறது",
    },
    "tool_agent.status.balance": {
        "en": "Checking your balance", "si": "ඔබගේ ශේෂය පරීක්ෂා කරමින්", "ta": "உங்கள் இருப்பைச் சரிபார்க்கிறது",
    },
    "tool_agent.status.bill": {
        "en": "Checking your bills", "si": "ඔබගේ බිල්පත් පරීක්ෂා කරමින්", "ta": "உங்கள் கட்டணப் பட்டியலைச் சரிபார்க்கிறது",
    },
    "tool_agent.status.account": {
        "en": "Checking your account", "si": "ඔබගේ ගිණුම පරීක්ෂා කරමින්", "ta": "உங்கள் கணக்கைச் சரிபார்க்கிறது",
    },
    "tool_agent.status.fault": {
        "en": "Checking your faults", "si": "ඔබගේ දෝෂ පරීක්ෂා කරමින්", "ta": "உங்கள் பழுதுகளைச் சரிபார்க்கிறது",
    },
    "tool_agent.status.packages": {
        "en": "Checking packages", "si": "පැකේජ පරීක්ෂා කරමින්", "ta": "தொகுப்புகளைச் சரிபார்க்கிறது",
    },
    "tool_agent.status.usage": {
        "en": "Checking your usage", "si": "ඔබගේ භාවිතය පරීක්ෂා කරමින්", "ta": "உங்கள் பயன்பாட்டைச் சரிபார்க்கிறது",
    },
}

STATUS_BY_TOOL = {
    "search_knowledgebase_tool": "tool_agent.status.search",
    "show_gb_balance": "tool_agent.status.balance",
    "show_lte_wallet_detail": "tool_agent.status.balance",
    "show_lte_purchased_packages": "tool_agent.status.balance",
    "show_bill_history": "tool_agent.status.bill",
    "show_last_payment": "tool_agent.status.bill",
    "show_reload_history": "tool_agent.status.bill",
    "show_service_detail": "tool_agent.status.account",
    "show_lte_subscriber_status": "tool_agent.status.account",
    "show_fault_list": "tool_agent.status.fault",
    "show_extra_gb_packages": "tool_agent.status.packages",
    "show_vas_data_bundle_packages": "tool_agent.status.packages",
    "compare_package": "tool_agent.status.packages",
    "show_daily_usage": "tool_agent.status.usage",
}


def short(language: Optional[str]) -> str:
    lang = (language or "en").strip().lower()[:2]
    return lang if lang in LANGUAGE_NAMES else "en"


def language_name(language: Optional[str]) -> str:
    return LANGUAGE_NAMES[short(language)]


def text(key: str, language: Optional[str] = None, **values) -> str:
    """The translated text of key, formatted with values and the official dial
    numbers. Never raises, never empty."""
    lang = short(language)
    merged = {**_dial(), **values}
    try:
        from i18n import t

        found = t(key, lang, **merged)
    except Exception:  # noqa: BLE001
        found = key
    if isinstance(found, str) and found and found != key:
        return found
    table = BUILTIN.get(key)
    if table is None:
        return key
    template = table.get(lang) or table["en"]
    try:
        return template.format(**merged)
    except (KeyError, IndexError):
        return template


def dial_text(key: str, language: Optional[str] = None) -> str:
    """A text that must carry the official numbers; a translation without them
    is replaced by the built in one."""
    found = text(key, language)
    dial = _dial()
    if all(number in found for number in dial.values()):
        return found
    return BUILTIN[key][short(language)].format(**dial)


VOICE_RULES = (
    "Speak in short sentences. Copy every number, price, date and name exactly as given, "
    "and say amounts with their unit. If there are many items, mention only the first three "
    "and offer to read the rest."
)


def guest_grounded_clause(language: Optional[str]) -> str:
    return (
        "Answer ONLY from the SLT excerpts below. Copy numbers, prices, package names and dates exactly as written. "
        "If the excerpts do not contain the answer, say so plainly and do not guess. "
        + VOICE_RULES + f" Reply in {language_name(language)}."
    )


def no_facts_clause(language: Optional[str]) -> str:
    return (
        "This message is conversation, not an SLT question. Do NOT state any SLT specific facts from your own "
        "knowledge: no packages, prices, services, coverage, contact details or account data. If the user wants SLT "
        "information, ask what they would like to know so it can be looked up. "
        + VOICE_RULES + f" Reply in {language_name(language)}."
    )


def customer_clause(language: Optional[str], missing: str = "") -> str:
    lines = [
        "The customer is verified and the SLT ACCOUNT DATA block below belongs to them.",
        "Answer only from that block. If the figure asked for is not in it, say you do not have it and do not guess "
        "or invent amounts, dates, balances or statuses.",
        "Never read out or repeat the telephone number, account numbers, tokens or codes.",
        "You can only read account information. You cannot buy, activate, change, add, subscribe or cancel anything.",
        VOICE_RULES,
    ]
    if missing:
        lines.append(f"Not available for this question: {missing}. Say so and do not make it up.")
    lines.append(f"Reply in {language_name(language)}.")
    return " ".join(lines)
