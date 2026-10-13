"""Is a message only conversation (greeting, thanks, goodbye, repeat, who are
you) with nothing about SLT to look up?

The whole message must be made of such phrases. One real word more makes it a
question. English, Sinhala, Tamil and the Latin letter forms are covered.
When unsure the answer is False, which costs one knowledge search and nothing
else, so this list stays short on purpose.
"""

import re

from access.policy import normalize

_PHRASES = (
    # English
    r"hello|hi|hey|hiya|good (?:morning|afternoon|evening|night|day)|how are you|how r u|thanks|thank you|thank u|"
    r"thanks a lot|many thanks|much appreciated|cheers|bye|goodbye|good bye|see you|see ya|take care|ok|okay|"
    r"alright|all right|great|nice|cool|fine|sure|yes|yeah|yep|no|nope|please|sorry|pardon|excuse me|"
    r"can you repeat(?: that)?|could you repeat(?: that)?|repeat(?: that)?|say that again|say it again|"
    r"once more|one more time|come again|what did you say|i did not (?:get|catch) that|i didn t (?:get|catch) that|"
    r"i do not understand|i don t understand|what do you mean|can you explain(?: that)?|what is your name|"
    r"who are you|what are you|what can you do|are you a (?:bot|robot|human)|are you real|"
    r"hold on|wait|one moment|just a (?:second|moment|minute)|you are welcome|welcome|"
    # Latin letter Sinhala and Tamil
    r"ayubowan|aubowan|hari|hari hari|ow|ou|nehe|na|istuti|isthuti|sthuti|stuti|bohoma istuti|bohoma sthuti|"
    r"nanri|nandri|vanakkam|sari|sandosham|poitu varen|poitu varaen|poitu varuven|"
    r"mama kiwwe|aye kiyanna|aye kiyanawada|eka aye kiyanna|oya kawda|oyaa kawda|oyage nama mokakda|"
    r"neengal yaar|ungal peyar enna|marubadiyum sollunga|"
    # Sinhala script
    r"ආයුබෝවන්|හෙලෝ|හලෝ|හායි|සුභ උදෑසනක්|සුභ දහවලක්|සුභ සන්ධ්‍යාවක්|සුභ රාත්‍රියක්|ස්තූතියි|ස්තුතියි|බොහොම ස්තූතියි|"
    r"බොහොම ස්තුතියි|ගිහින් එන්නම්|ගිහින් එන්න|හොඳයි|හරි|ඔව්|නැහැ|නෑ|කරුණාකර|සමාවෙන්න|නැවත කියන්න|"
    r"ආපහු කියන්න|ආයෙත් කියන්න|මට තේරුණේ නැහැ|තේරුණේ නැහැ|ඔබ කවුද|ඔයා කවුද|ඔබේ නම මොකක්ද|ඔයාගේ නම මොකක්ද|"
    r"කොහොමද|ඔබට කොහොමද|ටිකක් ඉන්න|"
    # Tamil script
    r"வணக்கம்|ஹலோ|ஹாய்|காலை வணக்கம்|மாலை வணக்கம்|இரவு வணக்கம்|நன்றி|மிக்க நன்றி|போய் வருகிறேன்|பிறகு பார்க்கலாம்|"
    r"சரி|ஆமாம்|ஆம்|இல்லை|தயவுசெய்து|மன்னிக்கவும்|மீண்டும் சொல்லுங்கள்|மறுபடியும் சொல்லுங்கள்|"
    r"எனக்குப் புரியவில்லை|புரியவில்லை|நீங்கள் யார்|நீ யார்|உங்கள் பெயர் என்ன|எப்படி இருக்கிறீர்கள்|ஒரு நிமிடம்"
)
_SMALLTALK = re.compile(r"(?<!\w)(?:" + _PHRASES + r")(?!\w)", re.IGNORECASE)
_FILLER = re.compile(r"(?<!\w)(?:to|you|u|so|very|much|lot|a|the|there|again|me|my|dear|sir|madam|bro|anna|aiyo|ah|um|uh)(?!\w)")


def is_smalltalk(text: str) -> bool:
    if not isinstance(text, str):
        return False
    rest = re.sub(r"['’]", " ", normalize(text))
    if not rest or len(rest) > 80:
        return False
    rest = _SMALLTALK.sub(" ", rest)
    rest = _FILLER.sub(" ", rest)
    return not re.search(r"\w", rest)
