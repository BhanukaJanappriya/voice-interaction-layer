"""Every sentence this package says, in English, Sinhala and Tamil.

tr() asks the shared i18n package first (the lead merges these same keys into
backend/i18n/*.json from docs/patches/agent_d_i18n.json) and falls back to the
built in text here, so the package works before and after the merge.

The Sinhala and Tamil texts are machine written and need a review by a native
speaker.
"""

import re
from typing import Optional

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

EN = {
    "confirm.option.BB": "Broadband",
    "confirm.option.VOICE": "Voice",
    "confirm.option.PEOTV": "PEO TV",
    "confirm.option.OTHER": "Other",
    "confirm.title.purchase_extra_gb": "Buy an extra data package",
    "confirm.title.add_vas_data_bundle": "Add a data bundle",
    "confirm.title.subscribe_lte_prepaid_package": "Subscribe to a package",
    "confirm.title.log_fault": "Log a fault",
    "confirm.label.package": "Package",
    "confirm.label.price": "Price",
    "confirm.label.validity": "Validity",
    "confirm.label.connection": "Connection",
    "confirm.label.service": "Service",
    "confirm.label.contact": "Contact number",
    "confirm.label.problem": "Problem",
    "confirm.ending": "ending {digits}",
    "confirm.not_provided": "not provided",
    "confirm.ask": "{action}: {details}. Please say yes or tap Confirm within {seconds} seconds to go ahead, or say no to cancel.",
    "confirm.cancelled": "I cancelled that. Nothing was changed.",
    "confirm.expired": "That request timed out, so I did not do it. Nothing was changed. Ask me again if you still want it.",
    "confirm.invalid": "I could not find a request waiting for your answer. Nothing was changed.",
    "confirm.disabled": "This feature is not switched on, so nothing was changed.",
    "confirm.off": "I can't make changes through this assistant yet, so nothing was changed. Please dial the hotline {hotline} (or {prepaid} for CDMA prepaid) and they will help you.",
    "confirm.failed_lead": "The request was not completed. SLT replied:",
    "confirm.unreachable": "I could not reach SLT, so I cannot tell whether it went through. I did not try again. Please check your account or dial {hotline} before asking again.",
    "confirm.reauth": "Your sign in has ended, so nothing was changed. Please verify again.",
    "confirm.refused": "I am not allowed to do that, so nothing was changed.",
    "confirm.done": "Done.",
    "confirm.pkg.unreadable": "I could not read the package list right now. Nothing was changed. Please try again later.",
    "confirm.pkg.none": "I could not find a package that matches. Nothing was changed. Please tell me the package name again.",
    "confirm.pkg.which": "Which package do you mean? {options}",
    "confirm.pkg.unknown_service": "I cannot tell which kind of connection this is, so I cannot buy a package. Please dial {hotline}.",
    "confirm.fault.ask_service": "Which service is the fault with: broadband, voice, PEO TV or other?",
    "confirm.fault.ask_which": "Which connection is it? Reply with the number: {options}",
    "confirm.fault.ask_contact": "What contact number should SLT call you on? Say skip if you do not want to give one.",
    "confirm.fault.ask_nature": "In a few words, what is the problem? Say skip to leave it blank.",
    "confirm.fault.none_found": "I could not find a service to log a fault for. Please dial {hotline}.",
    "confirm.fault.stopped": "I stopped the fault report. Nothing was logged.",
    "confirm.fault.bad_choice": "I did not understand that. Please reply with one of the numbers.",
    "confirm.status.packages": "Checking the available packages",
    "confirm.status.fault": "Preparing your fault report",
    "confirm.status.executing": "Making the change",
}

SI = {
    "confirm.option.BB": "බ්‍රෝඩ්බෑන්ඩ්",
    "confirm.option.VOICE": "හඬ",
    "confirm.option.PEOTV": "PEO TV",
    "confirm.option.OTHER": "වෙනත්",
    "confirm.title.purchase_extra_gb": "අමතර දත්ත පැකේජයක් මිලදී ගැනීම",
    "confirm.title.add_vas_data_bundle": "දත්ත බණ්ඩලයක් එකතු කිරීම",
    "confirm.title.subscribe_lte_prepaid_package": "පැකේජයකට දායක වීම",
    "confirm.title.log_fault": "දෝෂයක් වාර්තා කිරීම",
    "confirm.label.package": "පැකේජය",
    "confirm.label.price": "මිල",
    "confirm.label.validity": "වලංගු කාලය",
    "confirm.label.connection": "සම්බන්ධතාව",
    "confirm.label.service": "සේවාව",
    "confirm.label.contact": "සම්බන්ධ කරගත හැකි අංකය",
    "confirm.label.problem": "ගැටලුව",
    "confirm.ending": "අවසන් ඉලක්කම් {digits}",
    "confirm.not_provided": "ලබා දී නැත",
    "confirm.ask": "{action}: {details}. ඉදිරියට යාමට තත්පර {seconds} ක් ඇතුළත ඔව් යැයි කියන්න හෝ තහවුරු කරන්න ඔබන්න, අවලංගු කිරීමට නැහැ යැයි කියන්න.",
    "confirm.cancelled": "මම එය අවලංගු කළා. කිසිවක් වෙනස් කර නැත.",
    "confirm.expired": "එම ඉල්ලීම කල් ඉකුත් වූ නිසා මම එය කළේ නැත. කිසිවක් වෙනස් කර නැත. තවමත් අවශ්‍ය නම් නැවත ඉල්ලන්න.",
    "confirm.invalid": "ඔබේ පිළිතුර බලා සිටින ඉල්ලීමක් මට හමු නොවීය. කිසිවක් වෙනස් කර නැත.",
    "confirm.disabled": "මෙම පහසුකම සක්‍රිය කර නැති නිසා කිසිවක් වෙනස් කර නැත.",
    "confirm.off": "මට තවමත් මෙම සහායක හරහා වෙනස්කම් කළ නොහැක, එබැවින් කිසිවක් වෙනස් කර නැත. කරුණාකර හොට්ලයින් {hotline} (CDMA ප්‍රීපේඩ් සඳහා {prepaid}) අමතන්න.",
    "confirm.failed_lead": "ඉල්ලීම සම්පූර්ණ නොවීය. SLT පිළිතුරු දුන්නේ:",
    "confirm.unreachable": "මට SLT වෙත ළඟා විය නොහැකි වූ නිසා එය සිදු වූයේදැයි කිව නොහැක. මම නැවත උත්සාහ නොකළෙමි. නැවත ඉල්ලීමට පෙර ඔබේ ගිණුම පරීක්ෂා කරන්න හෝ {hotline} අමතන්න.",
    "confirm.reauth": "ඔබේ පිවිසුම අවසන් වී ඇති නිසා කිසිවක් වෙනස් කර නැත. කරුණාකර නැවත තහවුරු වන්න.",
    "confirm.refused": "මට එය කිරීමට අවසර නැති නිසා කිසිවක් වෙනස් කර නැත.",
    "confirm.done": "අවසන්.",
    "confirm.pkg.unreadable": "මට දැන් පැකේජ ලැයිස්තුව කියවිය නොහැකි විය. කිසිවක් වෙනස් කර නැත. පසුව නැවත උත්සාහ කරන්න.",
    "confirm.pkg.none": "ගැළපෙන පැකේජයක් හමු නොවීය. කිසිවක් වෙනස් කර නැත. කරුණාකර පැකේජයේ නම නැවත කියන්න.",
    "confirm.pkg.which": "ඔබ අදහස් කරන්නේ කුමන පැකේජයද? {options}",
    "confirm.pkg.unknown_service": "මෙය කුමන වර්ගයේ සම්බන්ධතාවක්දැයි කිව නොහැකි නිසා පැකේජයක් මිලදී ගත නොහැක. කරුණාකර {hotline} අමතන්න.",
    "confirm.fault.ask_service": "දෝෂය කුමන සේවාවටද: බ්‍රෝඩ්බෑන්ඩ්, හඬ, PEO TV හෝ වෙනත්?",
    "confirm.fault.ask_which": "කුමන සම්බන්ධතාවද? අංකයෙන් පිළිතුරු දෙන්න: {options}",
    "confirm.fault.ask_contact": "SLT ඔබට අමතන්නේ කුමන අංකයටද? අංකයක් දීමට අකමැති නම් මඟ හරින්න යැයි කියන්න.",
    "confirm.fault.ask_nature": "ගැටලුව කුමක්දැයි වචන කිහිපයකින් කියන්න. හිස්ව තැබීමට මඟ හරින්න යැයි කියන්න.",
    "confirm.fault.none_found": "දෝෂය වාර්තා කිරීමට සේවාවක් හමු නොවීය. කරුණාකර {hotline} අමතන්න.",
    "confirm.fault.stopped": "මම දෝෂ වාර්තාව නැවැත්වූවා. කිසිවක් වාර්තා කර නැත.",
    "confirm.fault.bad_choice": "මට එය තේරුණේ නැත. කරුණාකර එක් අංකයකින් පිළිතුරු දෙන්න.",
    "confirm.status.packages": "ලබා ගත හැකි පැකේජ පරීක්ෂා කරමින්",
    "confirm.status.fault": "ඔබේ දෝෂ වාර්තාව සකසමින්",
    "confirm.status.executing": "වෙනස්කම සිදු කරමින්",
}

TA = {
    "confirm.option.BB": "பிராட்பேண்ட்",
    "confirm.option.VOICE": "குரல்",
    "confirm.option.PEOTV": "PEO TV",
    "confirm.option.OTHER": "வேறு",
    "confirm.title.purchase_extra_gb": "கூடுதல் டேட்டா பேக்கேஜ் வாங்குதல்",
    "confirm.title.add_vas_data_bundle": "டேட்டா பண்டில் சேர்த்தல்",
    "confirm.title.subscribe_lte_prepaid_package": "பேக்கேஜுக்கு சந்தா செலுத்துதல்",
    "confirm.title.log_fault": "பழுதைப் பதிவு செய்தல்",
    "confirm.label.package": "பேக்கேஜ்",
    "confirm.label.price": "விலை",
    "confirm.label.validity": "செல்லுபடியாகும் காலம்",
    "confirm.label.connection": "இணைப்பு",
    "confirm.label.service": "சேவை",
    "confirm.label.contact": "தொடர்பு எண்",
    "confirm.label.problem": "பிரச்சினை",
    "confirm.ending": "கடைசி இலக்கங்கள் {digits}",
    "confirm.not_provided": "வழங்கப்படவில்லை",
    "confirm.ask": "{action}: {details}. தொடர {seconds} விநாடிகளுக்குள் ஆம் என்று சொல்லுங்கள் அல்லது உறுதிப்படுத்து என்பதை அழுத்துங்கள்; ரத்து செய்ய இல்லை என்று சொல்லுங்கள்.",
    "confirm.cancelled": "அதை ரத்து செய்தேன். எதுவும் மாற்றப்படவில்லை.",
    "confirm.expired": "அந்த கோரிக்கை காலாவதியானதால் நான் அதைச் செய்யவில்லை. எதுவும் மாற்றப்படவில்லை. இன்னும் வேண்டுமானால் மீண்டும் கேளுங்கள்.",
    "confirm.invalid": "உங்கள் பதிலுக்காகக் காத்திருக்கும் கோரிக்கை எதுவும் இல்லை. எதுவும் மாற்றப்படவில்லை.",
    "confirm.disabled": "இந்த வசதி இயக்கப்படவில்லை, எனவே எதுவும் மாற்றப்படவில்லை.",
    "confirm.off": "இந்த உதவியாளர் மூலம் என்னால் இன்னும் மாற்றங்களைச் செய்ய முடியாது, எனவே எதுவும் மாற்றப்படவில்லை. தயவுசெய்து ஹாட்லைன் {hotline} (CDMA ப்ரீபெய்டுக்கு {prepaid}) அழையுங்கள்.",
    "confirm.failed_lead": "கோரிக்கை நிறைவடையவில்லை. SLT பதில்:",
    "confirm.unreachable": "SLT-ஐ தொடர்பு கொள்ள முடியாததால் அது நடந்ததா என்று சொல்ல முடியவில்லை. நான் மீண்டும் முயற்சிக்கவில்லை. மீண்டும் கேட்கும் முன் உங்கள் கணக்கைச் சரிபார்க்கவும் அல்லது {hotline} அழைக்கவும்.",
    "confirm.reauth": "உங்கள் உள்நுழைவு முடிந்துவிட்டதால் எதுவும் மாற்றப்படவில்லை. தயவுசெய்து மீண்டும் சரிபார்க்கவும்.",
    "confirm.refused": "அதைச் செய்ய எனக்கு அனுமதி இல்லை, எனவே எதுவும் மாற்றப்படவில்லை.",
    "confirm.done": "முடிந்தது.",
    "confirm.pkg.unreadable": "பேக்கேஜ் பட்டியலை இப்போது படிக்க முடியவில்லை. எதுவும் மாற்றப்படவில்லை. பிறகு முயற்சிக்கவும்.",
    "confirm.pkg.none": "பொருந்தும் பேக்கேஜ் கிடைக்கவில்லை. எதுவும் மாற்றப்படவில்லை. பேக்கேஜின் பெயரை மீண்டும் சொல்லுங்கள்.",
    "confirm.pkg.which": "எந்த பேக்கேஜ் என்று சொல்லுங்கள்? {options}",
    "confirm.pkg.unknown_service": "இது எந்த வகை இணைப்பு என்று தெரியாததால் பேக்கேஜ் வாங்க முடியாது. தயவுசெய்து {hotline} அழையுங்கள்.",
    "confirm.fault.ask_service": "பழுது எந்த சேவையில்: பிராட்பேண்ட், குரல், PEO TV அல்லது வேறு?",
    "confirm.fault.ask_which": "எந்த இணைப்பு? எண்ணைச் சொல்லி பதிலளியுங்கள்: {options}",
    "confirm.fault.ask_contact": "SLT உங்களை எந்த எண்ணில் அழைக்க வேண்டும்? கொடுக்க விரும்பவில்லை என்றால் தவிர் என்று சொல்லுங்கள்.",
    "confirm.fault.ask_nature": "பிரச்சினை என்ன என்று சில வார்த்தைகளில் சொல்லுங்கள். காலியாக விட தவிர் என்று சொல்லுங்கள்.",
    "confirm.fault.none_found": "பழுதைப் பதிவு செய்ய சேவை எதுவும் கிடைக்கவில்லை. தயவுசெய்து {hotline} அழையுங்கள்.",
    "confirm.fault.stopped": "பழுது அறிக்கையை நிறுத்தினேன். எதுவும் பதிவு செய்யப்படவில்லை.",
    "confirm.fault.bad_choice": "எனக்குப் புரியவில்லை. தயவுசெய்து ஒரு எண்ணுடன் பதிலளியுங்கள்.",
    "confirm.status.packages": "கிடைக்கும் பேக்கேஜ்களைச் சரிபார்க்கிறது",
    "confirm.status.fault": "உங்கள் பழுது அறிக்கையைத் தயாரிக்கிறது",
    "confirm.status.executing": "மாற்றத்தைச் செய்கிறது",
}

BUILTIN = {"en": EN, "si": SI, "ta": TA}


def lang_of(language: Optional[str]) -> str:
    short = (language or "en").strip().lower()[:2]
    return short if short in BUILTIN else "en"


def _fill(template: str, values: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def tr(key: str, language: Optional[str] = None, **values) -> str:
    """The text for key in the language; never raises, never returns empty."""
    lang = lang_of(language)
    try:
        from i18n import t  # lazy: the shared package may not hold these keys yet

        found = t(key, lang, **values)
        if isinstance(found, str) and found and found != key:
            return found
    except Exception:  # noqa: BLE001
        pass
    template = BUILTIN[lang].get(key) or EN.get(key) or key
    return _fill(template, values)


def dial() -> dict:
    """Official numbers, the same source as the guest block message."""
    try:
        from access.messages import _dial

        return dict(_dial())
    except Exception:  # noqa: BLE001
        return {"hotline": "1212", "sms": "0113121212", "prepaid": "1222"}
