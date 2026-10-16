# Translations review

Written 2026-10-15 by Agent D. **All Sinhala and Tamil texts below are machine written. None has been read by a native speaker.** They follow the terminology already used in `frontend/i18n/si.json`, `frontend/i18n/ta.json` and the backend files. Fill the verdict column with OK, or with the corrected wording, then copy corrections into `docs/patches/D_i18n_keys.json` before the keys are merged.

Register: polite spoken register for a telecom service (Sinhala: you are addressed with ඔබ, requests use කරුණාකර; Tamil: polite imperative with தயவுசெய்து). Brand and product words (SLT, Mobitel, PIN, SMS, Gemini Live, LiveKit) stay in Latin script on purpose.

Verdict values: OK, CHANGE (write the new text), UNSURE.

## Frontend (frontend/i18n)

| Key | English source | Sinhala | Tamil | Where used | Sinhala verdict | Tamil verdict |
|-|-|-|-|-|-|-|
| `app.title` | SLT Mobitel Voice Assistant | SLT Mobitel හඬ සහායකයා | SLT Mobitel குரல் உதவியாளர் | document.title, index.html title | | |
| `a11y.theme_toggle` | Switch between dark and light theme | අඳුරු සහ ආලෝක තේමාව මාරු කරන්න | இருண்ட மற்றும் ஒளி தீமை மாற்றவும் | theme button aria-label | | |
| `mic.title` | Enable your microphone | ඔබේ මයික්‍රෝෆෝනය සක්‍රීය කරන්න | உங்கள் மைக்ரோஃபோனை இயக்கவும் | microphone modal title | | |
| `mic.body` | This assistant needs microphone access to hear you speak. Your browser will ask you to confirm right after. | ඔබේ කතාව ඇසීමට මෙම සහායකයාට මයික්‍රෝෆෝනයට ප්‍රවේශය අවශ්‍ය වේ. ඉන්පසු ඔබේ බ්‍රවුසරය තහවුරු කරන ලෙස ඔබෙන් ඉල්ලනු ඇත. | நீங்கள் பேசுவதைக் கேட்க இந்த உதவியாளருக்கு மைக்ரோஃபோன் அணுகல் தேவை. அதன் பிறகு உங்கள் உலாவி உறுதிப்படுத்தும்படி கேட்கும். | microphone modal body | | |
| `mic.allow` | Allow microphone | මයික්‍රෝෆෝනයට ඉඩ දෙන්න | மைக்ரோஃபோனை அனுமதி | microphone modal button | | |
| `mic.not_now` | Not now | දැන් නොවේ | இப்போது வேண்டாம் | microphone modal button | | |
| `select.tagline` | How would you like to talk today? | අද ඔබ කතා කිරීමට කැමති කෙසේද? | இன்று நீங்கள் எப்படி பேச விரும்புகிறீர்கள்? | select screen | | |
| `select.chat` | Start Chat | කතාබහ ආරම්භ කරන්න | அரட்டையைத் தொடங்கு | select screen button | | |
| `select.voice` | Start Voice | හඬ ආරම්භ කරන්න | குரலைத் தொடங்கு | select screen button | | |
| `nav.change_language` | Change language | භාෂාව වෙනස් කරන්න | மொழியை மாற்று | select screen link | | |
| `nav.back` | Back | ආපසු | பின்செல் | back links on the user and chat screens | | |
| `user.tagline` | Are you a new or existing user? | ඔබ නව පාරිභෝගිකයෙක්ද, පවතින පාරිභෝගිකයෙක්ද? | நீங்கள் புதிய வாடிக்கையாளரா அல்லது தற்போதைய வாடிக்கையாளரா? | user screen | | |
| `user.existing` | Existing user | පවතින පාරිභෝගිකයා | தற்போதைய வாடிக்கையாளர் | user screen button | | |
| `user.new` | New user | නව පාරිභෝගිකයා | புதிய வாடிக்கையாளர் | user screen button | | |
| `legacy.phone_label` | Your landline number | ඔබේ ස්ථාවර දුරකථන අංකය | உங்கள் நிலைத் தொலைபேசி எண் | legacy PIN form | | |
| `legacy.pin_label` | PIN | PIN අංකය | PIN எண் | legacy PIN form | | |
| `legacy.pin_placeholder` | 4 to 6 digits | ඉලක්කම් 4 සිට 6 දක්වා | 4 முதல் 6 இலக்கங்கள் | legacy PIN form | | |
| `legacy.otp_label` | Verification code | තහවුරු කිරීමේ කේතය | சரிபார்ப்புக் குறியீடு | legacy PIN form | | |
| `legacy.otp_placeholder` | 6 digit code | ඉලක්කම් 6ක කේතය | 6 இலக்கக் குறியீடு | legacy PIN form | | |
| `legacy.resend` | Resend code | නව කේතයක් එවන්න | புதிய குறியீட்டை அனுப்பு | legacy PIN form | | |
| `legacy.resend_in` | Resend code in {seconds}s | තත්පර {seconds}කින් නැවත එවිය හැක | {seconds} வினாடிகளில் மீண்டும் அனுப்பலாம் | legacy PIN form | | |
| `legacy.continue` | Continue | ඉදිරියට | தொடரவும் | legacy PIN form | | |
| `legacy.send_code` | Send code | කේතය එවන්න | குறியீட்டை அனுப்பு | legacy PIN form | | |
| `legacy.verify` | Verify | තහවුරු කරන්න | சரிபார் | legacy PIN form | | |
| `legacy.use_otp` | Send me a one time code instead | ඒ වෙනුවට එක් වරක් භාවිත කරන කේතයක් එවන්න | அதற்குப் பதிலாக ஒருமுறை பயன்படுத்தும் குறியீட்டை அனுப்பவும் | legacy PIN form | | |
| `legacy.use_pin` | Use my PIN instead | ඒ වෙනුවට මගේ PIN අංකය භාවිත කරන්න | அதற்குப் பதிலாக என் PIN எண்ணைப் பயன்படுத்துகிறேன் | legacy PIN form | | |
| `legacy.use_other` | Use a different option | වෙනත් ක්‍රමයක් භාවිත කරන්න | வேறு முறையைப் பயன்படுத்து | legacy PIN form | | |
| `legacy.otp_sent` | We sent a code to {masked}. It is valid for {minutes} minutes. | {masked} වෙත කේතයක් එවා ඇත. එය විනාඩි {minutes}ක් වලංගුයි. | {masked} க்கு குறியீடு அனுப்பப்பட்டுள்ளது. இது {minutes} நிமிடங்கள் செல்லுபடியாகும். | legacy PIN form | | |
| `legacy.err.enter_phone` | Please enter your landline number. | කරුණාකර ඔබේ ස්ථාවර දුරකථන අංකය ඇතුළත් කරන්න. | தயவுசெய்து உங்கள் நிலைத் தொலைபேசி எண்ணை உள்ளிடவும். | legacy PIN form error | | |
| `legacy.err.enter_phone_pin` | Please enter your landline number and PIN. | කරුණාකර ඔබේ ස්ථාවර දුරකථන අංකය සහ PIN අංකය ඇතුළත් කරන්න. | தயவுசெய்து உங்கள் நிலைத் தொலைபேசி எண்ணையும் PIN எண்ணையும் உள்ளிடவும். | legacy PIN form error | | |
| `legacy.err.enter_code` | Please enter the code we sent you. | කරුණාකර අප එවූ කේතය ඇතුළත් කරන්න. | நாங்கள் அனுப்பிய குறியீட்டை உள்ளிடவும். | legacy PIN form error | | |
| `legacy.err.phone_pin_format` | Please enter a valid Sri Lankan landline number and a 4 to 6 digit PIN. | කරුණාකර වලංගු ශ්‍රී ලාංකික ස්ථාවර දුරකථන අංකයක් සහ ඉලක්කම් 4 සිට 6 දක්වා PIN අංකයක් ඇතුළත් කරන්න. | தயவுசெய்து சரியான இலங்கை நிலைத் தொலைபேசி எண்ணையும் 4 முதல் 6 இலக்க PIN எண்ணையும் உள்ளிடவும். | legacy PIN form error | | |
| `legacy.err.code_locked` | Too many wrong codes. Please request a new code. | වැරදි කේත බොහෝ වාර ගණනක් ඇතුළත් කර ඇත. කරුණාකර නව කේතයක් ඉල්ලන්න. | தவறான குறியீடுகள் அதிக முறை உள்ளிடப்பட்டன. தயவுசெய்து புதிய குறியீட்டைக் கேளுங்கள். | legacy PIN form error | | |
| `legacy.err.wait_seconds` | Please wait {seconds} seconds before trying again. | නැවත උත්සාහ කිරීමට පෙර කරුණාකර තත්පර {seconds}ක් රැඳී සිටින්න. | மீண்டும் முயற்சிக்கும் முன் {seconds} வினாடிகள் காத்திருக்கவும். | legacy PIN form error | | |
| `legacy.err.too_many` | Too many attempts. Please wait a moment and try again. | උත්සාහ කිරීම් වැඩි වැඩියි. කරුණාකර මදක් රැඳී සිට නැවත උත්සාහ කරන්න. | அதிக முறை முயற்சிக்கப்பட்டது. சிறிது நேரம் காத்திருந்து மீண்டும் முயற்சிக்கவும். | legacy PIN form error | | |
| `legacy.err.device_limit` | Too many attempts from this device. Please wait a minute and try again. | මෙම උපාංගයෙන් උත්සාහ කිරීම් වැඩි වැඩියි. කරුණාකර විනාඩියක් රැඳී සිට නැවත උත්සාහ කරන්න. | இந்தச் சாதனத்திலிருந்து அதிக முறை முயற்சிக்கப்பட்டது. ஒரு நிமிடம் காத்திருந்து மீண்டும் முயற்சிக்கவும். | legacy PIN form error | | |
| `legacy.err.otp_unavailable` | One time codes are not available right now. Please use your PIN. | එක් වරක් භාවිත කරන කේත දැන් ලබා ගත නොහැක. කරුණාකර ඔබේ PIN අංකය භාවිත කරන්න. | ஒருமுறை பயன்படுத்தும் குறியீடுகள் இப்போது கிடைக்கவில்லை. உங்கள் PIN எண்ணைப் பயன்படுத்தவும். | legacy PIN form error | | |
| `legacy.err.code_wrong` | That code is not correct or has expired. | එම කේතය නිවැරදි නැත, නැතහොත් කල් ඉකුත් වී ඇත. | அந்தக் குறியீடு சரியில்லை அல்லது காலாவதியாகிவிட்டது. | legacy PIN form error | | |
| `legacy.err.account_locked` | Too many incorrect attempts. This account is locked for about {minutes} minute(s). | වැරදි උත්සාහ බොහෝ ගණනක් සිදු විය. මෙම ගිණුම විනාඩි {minutes}ක පමණ කාලයකට අගුළු දමා ඇත. | தவறான முயற்சிகள் அதிகம். இந்தக் கணக்கு சுமார் {minutes} நிமிடங்களுக்குப் பூட்டப்பட்டுள்ளது. | legacy PIN form error | | |
| `legacy.err.mismatch` | That number and PIN do not match our records. | එම අංකය සහ PIN අංකය අපගේ වාර්තාවලට ගැළපෙන්නේ නැත. | அந்த எண்ணும் PIN எண்ணும் எங்கள் பதிவுகளுடன் பொருந்தவில்லை. | legacy PIN form error | | |
| `voice.history` | View conversation history | සංවාද ඉතිහාසය බලන්න | உரையாடல் வரலாற்றைப் பார்க்கவும் | voice screen button aria-label | | |
| `voice.logout` | Log out | ඉවත් වන්න | வெளியேறு | voice screen button aria-label | | |
| `voice.gender.female` | Female voice and avatar. Switch to male | ගැහැණු හඬ සහ අවතාරය. පිරිමි බවට මාරු කරන්න | பெண் குரலும் அவதாரும். ஆணுக்கு மாற்றவும் | gender button aria-label | | |
| `voice.gender.male` | Male voice and avatar. Switch to female | පිරිමි හඬ සහ අවතාරය. ගැහැණු බවට මාරු කරන්න | ஆண் குரலும் அவதாரும். பெண்ணுக்கு மாற்றவும் | gender button aria-label | | |
| `voice.gender.letter_female` | F | ගැ | பெ | gender button letter | | |
| `voice.gender.letter_male` | M | පි | ஆ | gender button letter | | |
| `voice.captions` | Toggle captions | උපසිරැසි පෙන්වන්න හෝ සඟවන්න | வசனங்களைக் காட்டு அல்லது மறை | captions button aria-label | | |
| `voice.mic.start` | Start the conversation | සංවාදය ආරම්භ කරන්න | உரையாடலைத் தொடங்கவும் | mic button aria-label | | |
| `voice.mic.unmute` | Unmute microphone | මයික්‍රෝෆෝනයේ නිශ්ශබ්දතාව ඉවත් කරන්න | மைக்ரோஃபோனின் ஒலியடக்கத்தை நீக்கவும் | mic button aria-label | | |
| `voice.mic.mute` | Mute microphone | මයික්‍රෝෆෝනය නිශ්ශබ්ද කරන්න | மைக்ரோஃபோனை ஒலியடக்கவும் | mic button aria-label | | |
| `voice.end` | End the conversation | සංවාදය අවසන් කරන්න | உரையாடலை முடிக்கவும் | end button aria-label | | |
| `voice.mic_denied` | I could not use your microphone. Please allow microphone access in your browser and try again. | ඔබේ මයික්‍රෝෆෝනය භාවිත කළ නොහැකි විය. කරුණාකර බ්‍රවුසරයේ මයික්‍රෝෆෝනයට ඉඩ දී නැවත උත්සාහ කරන්න. | உங்கள் மைக்ரோஃபோனைப் பயன்படுத்த முடியவில்லை. உலாவியில் மைக்ரோஃபோன் அணுகலை அனுமதித்து மீண்டும் முயற்சிக்கவும். | setCaption on mic failure | | |
| `voice.tap_sound` | Tap anywhere to turn the sound on. | ශබ්දය සක්‍රීය කිරීමට ඕනෑම තැනක ස්පර්ශ කරන්න. | ஒலியை இயக்க திரையில் எங்கேயும் தொடவும். | setCaption when autoplay is blocked | | |
| `voice.male_missing` | The male avatar is not installed; the voice has changed. | පිරිමි අවතාරය ස්ථාපනය කර නැත; හඬ පමණක් වෙනස් විය. | ஆண் அவதார் நிறுவப்படவில்லை; குரல் மட்டும் மாறியுள்ளது. | setCaption on avatar fallback | | |
| `voice.reconnecting` | One moment, reconnecting | මොහොතක් රැඳී සිටින්න, නැවත සම්බන්ධ වෙමින් | ஒரு நிமிடம், மீண்டும் இணைக்கிறேன் | tier change notice (caption and spoken) | | |
| `voice.mode.gemini_live` | Gemini Live ({model}) | Gemini Live ({model}) | Gemini Live ({model}) | mode badge, tier 1 | | |
| `voice.mode.livekit` | LiveKit | LiveKit | LiveKit | mode badge, tier 2 | | |
| `voice.mode.local` | Local voice | දේශීය හඬ ක්‍රමය | உள்ளூர் குரல் முறை | mode badge, tier 3 | | |
| `mode.cloud` | Cloud assistant | ක්ලවුඩ් සහායකය | கிளவுட் உதவியாளர் | reply meta and chat badge (was cloud multi agent) | | |
| `mode.local_agent` | Local assistant | දේශීය සහායකය | உள்ளூர் உதவியாளர் | reply meta and chat badge (was local single agent) | | |
| `mode.policy` | Access rules reply | ප්‍රවේශ නීති පිළිතුර | அணுகல் விதிகளின் பதில் | reply meta (was access policy canned reply) | | |
| `mode.hub` | SLT information | SLT තොරතුරු | SLT தகவல் | reply meta (was SLT hub fixed reply) | | |
| `mode.account_unavailable` | Account data unavailable | ගිණුම් දත්ත ලබා ගත නොහැක | கணக்கு தரவு கிடைக்கவில்லை | reply meta (was account data unavailable) | | |
| `mode.plus_account` | {mode} and account data | {mode} සහ ගිණුම් දත්ත | {mode} மற்றும் கணக்கு தரவு | reply meta suffix (was + SLT account data) | | |
| `history.title` | Conversation history | සංවාද ඉතිහාසය | உரையாடல் வரலாறு | history modal title | | |
| `history.close` | Close history | ඉතිහාසය වසන්න | வரலாற்றை மூடு | history modal close aria-label | | |
| `chat.title` | Text chat | පෙළ කතාබහ | உரை அரட்டை | chat screen | | |
| `chat.empty` | Type a message below to start chatting. | කතාබහ ආරම්භ කිරීමට පහත පණිවුඩයක් ටයිප් කරන්න. | அரட்டையைத் தொடங்க கீழே ஒரு செய்தியை உள்ளிடவும். | chat screen hint | | |
| `chat.placeholder` | Type a message... | පණිවුඩයක් ටයිප් කරන්න... | செய்தியை உள்ளிடவும்... | chat input placeholder | | |
| `chat.send` | Send | යවන්න | அனுப்பு | chat send button | | |
| `chat.mode_none` | Agent mode: none yet | සහායක ක්‍රමය: තවම නැත | உதவியாளர் பயன்முறை: இன்னும் இல்லை | chat mode badge, empty | | |
| `chat.mode` | Agent mode: {mode} | සහායක ක්‍රමය: {mode} | உதவியாளர் பயன்முறை: {mode} | chat mode badge | | |
| `chat.intro_meta` | Introduction | හැඳින්වීම | அறிமுகம் | meta label under the first assistant bubble | | |
| `chat.guest` | Guest | අමුත්තා | விருந்தினர் | name used when nobody is verified (moved from CHAT_GREETINGS) | | |
| `chat.greeting` | Welcome to SLT Mobitel, {name}! I'm your voice assistant, and I understand English, Sinhala, and Tamil, so just start speaking whenever you're ready. | SLT Mobitel වෙත සාදරයෙන් පිළිගනිමු, {name}! මම ඔබේ හඬ සහායකයා. මට ඉංග්‍රීසි, සිංහල සහ දෙමළ භාෂා තේරෙනවා, ඒ නිසා ඔබ සූදානම් වූ විට කතා කිරීම ආරම්භ කරන්න. | SLT Mobitel-க்கு வரவேற்கிறோம், {name}! நான் உங்கள் குரல் உதவியாளர். எனக்கு ஆங்கிலம், சிங்களம் மற்றும் தமிழ் புரியும், எனவே நீங்கள் தயாரானதும் பேசத் தொடங்குங்கள். | text chat greeting (moved from CHAT_GREETINGS, text already existed in code) | | |
| `error.prefix` | Error: {message} | දෝෂය: {message} | பிழை: {message} | server error event shown in the transcript and caption | | |
| `error.unreachable` | Sorry, I could not reach the service. Please try again in a moment. | සමාවන්න, සේවාව වෙත සම්බන්ධ විය නොහැකි විය. කරුණාකර මදක් පසුව නැවත උත්සාහ කරන්න. | மன்னிக்கவும், சேவையை அடைய முடியவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயலவும். | replaces the developer messages about the backend URL and the backend not running | | |
| `footer.credit` | SLT Digital Lab, voice interaction prototype | SLT Digital Lab, හඬ අන්තර්ක්‍රියා මූලාකෘතිය | SLT Digital Lab, குரல் தொடர்பு முன்மாதிரி | page footer | | |

## Backend (backend/i18n)

| Key | English source | Sinhala | Tamil | Where used | Sinhala verdict | Tamil verdict |
|-|-|-|-|-|-|-|
| `voice.reconnecting` | One moment, reconnecting | මොහොතක් රැඳී සිටින්න, නැවත සම්බන්ධ වෙමින් | ஒரு நிமிடம், மீண்டும் இணைக்கிறேன் | voice_router/handover.py NOTICE_KEY (spoken) | | |
| `voice.guest` | Guest | අමුත්තා | விருந்தினர் | spoken greeting when no name (moved from server.py _GREETINGS) | | |
| `voice.greeting` | Welcome to SLT Mobitel, {name}! I'm your voice assistant, and I understand English, Sinhala, and Tamil, so just start speaking whenever you're ready. | SLT Mobitel වෙත සාදරයෙන් පිළිගනිමු, {name}! මම ඔබේ හඬ සහායකයා. මට ඉංග්‍රීසි, සිංහල සහ දෙමළ භාෂා තේරෙනවා, ඒ නිසා ඔබ සූදානම් වූ විට කතා කිරීම ආරම්භ කරන්න. | SLT Mobitel-க்கு வரவேற்கிறோம், {name}! நான் உங்கள் குரல் உதவியாளர். எனக்கு ஆங்கிலம், சிங்களம் மற்றும் தமிழ் புரியும், எனவே நீங்கள் தயாரானதும் பேசத் தொடங்குங்கள். | spoken greeting (moved from server.py _GREETINGS) | | |
| `voice.closing_question` | You're welcome! Is there anything else I can help you with, or shall I end our conversation here? | ඔබව සාදරයෙන් පිළිගනිමු! මට ඔබට තවත් යමක් උදව් කළ හැකිද, නැතහොත් අපගේ සංවාදය මෙතැනින් අවසන් කරන්නද? | மகிழ்ச்சி! நான் உங்களுக்கு வேறு ஏதாவது உதவ முடியுமா, அல்லது நமது உரையாடலை இங்கே முடித்துக்கொள்ளலாமா? | spoken closing question (moved from server.py _CLOSING_QUESTION_TEXT) | | |
| `voice.goodbye` | Thank you for using the SLT Mobitel voice assistant. Goodbye, and have a great day! | SLT Mobitel හඬ සහායකයා භාවිතා කළාට ස්තුතියි. ආයුබෝවන්, ඔබට සුබ දවසක්! | SLT Mobitel குரல் உதவியாளரைப் பயன்படுத்தியதற்கு நன்றி. வணக்கம், இனிய நாளாக அமையட்டும்! | spoken goodbye (moved from server.py _CLOSING_GOODBYE_TEXT) | | |
| `voice.no_mic_audio` | No microphone audio arrived, so the conversation is ending. | මයික්‍රෝෆෝන ශබ්දය ලැබුණේ නැති නිසා සංවාදය අවසන් කරනවා. | மைக்ரோஃபோன் ஒலி வராததால் உரையாடல் முடிவடைகிறது. | livekit_transport/session.py error message sent to the page | | |
| `voice.agent_down` | The voice assistant is not running right now. Please try again shortly. | හඬ සහායකයා දැන් ක්‍රියාත්මක නොවේ. කරුණාකර මදක් පසුව නැවත උත්සාහ කරන්න. | குரல் உதவியாளர் இப்போது இயங்கவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயலவும். | livekit_transport/tokens.py HTTP 503 detail | | |
| `llm.lost_connection` | Sorry, I lost my connection there. Could you ask that again? | සමාවන්න, මගේ සම්බන්ධතාව බිඳී ගියා. කරුණාකර නැවත අසන්න පුළුවන්ද? | மன்னிக்கவும், இணைப்பு துண்டிக்கப்பட்டது. தயவுசெய்து மீண்டும் கேட்க முடியுமா? | llm_chain/chain.py APOLOGY, agent.py | | |
| `llm.unavailable` | Sorry, I can't reach my language service right now. Please try again in a moment. | සමාවන්න, මගේ භාෂා සේවාව දැන් ලබා ගත නොහැක. කරුණාකර මදක් පසුව නැවත උත්සාහ කරන්න. | மன்னிக்கவும், என் மொழிச் சேவையை இப்போது அடைய முடியவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயலவும். | llm_chain/chain.py UNAVAILABLE_NOTICE | | |
| `llm.local_unavailable` | Sorry, I could not get an answer from the local assistant. Please try again in a moment. | සමාවන්න, දේශීය සහායකයාගෙන් පිළිතුරක් ලබා ගත නොහැකි විය. කරුණාකර මදක් පසුව නැවත උත්සාහ කරන්න. | மன்னிக்கவும், உள்ளூர் உதவியாளரிடமிருந்து பதில் பெற முடியவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயலவும். | agent.py Error: ... Ollama messages | | |
| `auth.account_locked` | Too many incorrect attempts. This account is locked; try again in about {minutes} minute(s). | වැරදි උත්සාහ බොහෝ ගණනක් සිදු විය. මෙම ගිණුම අගුළු දමා ඇත; විනාඩි {minutes}කින් පමණ නැවත උත්සාහ කරන්න. | தவறான முயற்சிகள் அதிகம். இந்தக் கணக்கு பூட்டப்பட்டுள்ளது; சுமார் {minutes} நிமிடங்களுக்குப் பிறகு மீண்டும் முயற்சிக்கவும். | auth/routes.py HTTP 423 | | |
| `auth.device_limit` | Too many attempts from this device. Please wait a minute and try again. | මෙම උපාංගයෙන් උත්සාහ කිරීම් වැඩි වැඩියි. කරුණාකර විනාඩියක් රැඳී සිට නැවත උත්සාහ කරන්න. | இந்தச் சாதனத்திலிருந்து அதிக முறை முயற்சிக்கப்பட்டது. ஒரு நிமிடம் காத்திருந்து மீண்டும் முயற்சிக்கவும். | auth/routes.py HTTP 429 | | |
| `auth.bad_credentials` | The phone number or PIN is incorrect. | දුරකථන අංකය හෝ PIN අංකය වැරදියි. | தொலைபேசி எண் அல்லது PIN எண் தவறானது. | auth/routes.py HTTP 401 | | |
| `auth.not_signed_in` | You are not signed in. | ඔබ පිවිසී නැත. | நீங்கள் உள்நுழையவில்லை. | auth/routes.py HTTP 401 | | |
| `otp.bad_code` | The code is incorrect or has expired. | කේතය වැරදියි, නැතහොත් කල් ඉකුත් වී ඇත. | குறியீடு தவறானது அல்லது காலாவதியாகிவிட்டது. | otp/routes.py | | |
| `otp.locked` | Too many incorrect codes. Please request a new code. | වැරදි කේත බොහෝ වාර ගණනක් ඇතුළත් කර ඇත. කරුණාකර නව කේතයක් ඉල්ලන්න. | தவறான குறியீடுகள் அதிக முறை உள்ளிடப்பட்டன. தயவுசெய்து புதிய குறியீட்டைக் கேளுங்கள். | otp/routes.py | | |
| `otp.too_soon` | A code was sent a moment ago. Please wait before asking for another. | මද වේලාවකට පෙර කේතයක් එවා ඇත. තවත් කේතයක් ඉල්ලීමට පෙර කරුණාකර රැඳී සිටින්න. | சற்று முன்பு ஒரு குறியீடு அனுப்பப்பட்டது. மற்றொன்றைக் கேட்கும் முன் காத்திருக்கவும். | otp/routes.py | | |
| `otp.rate_limited` | Too many attempts. Please try again later. | උත්සාහ කිරීම් වැඩි වැඩියි. කරුණාකර පසුව නැවත උත්සාහ කරන්න. | அதிக முறை முயற்சிக்கப்பட்டது. தயவுசெய்து பிறகு மீண்டும் முயற்சிக்கவும். | otp/routes.py | | |
| `otp.unavailable` | One time codes are not available right now. Please use your PIN. | එක් වරක් භාවිත කරන කේත දැන් ලබා ගත නොහැක. කරුණාකර ඔබේ PIN අංකය භාවිත කරන්න. | ஒருமுறை பயன்படுத்தும் குறியீடுகள் இப்போது கிடைக்கவில்லை. உங்கள் PIN எண்ணைப் பயன்படுத்தவும். | otp/routes.py | | |
| `otp.disabled` | One time code sign in is not enabled. | එක් වරක් භාවිත කරන කේතයෙන් පිවිසීම සක්‍රීය කර නැත. | ஒருமுறை பயன்படுத்தும் குறியீட்டில் உள்நுழைவு இயக்கப்படவில்லை. | otp/routes.py | | |
| `otp.destination_generic` | your registered mobile number | ඔබේ ලියාපදිංචි ජංගම දුරකථන අංකය | உங்கள் பதிவு செய்யப்பட்ட மொபைல் எண் | otp/hub_backend.py GENERIC_DESTINATION | | |
| `api.rate_limited` | Too many requests. Please wait a moment and try again. | ඉල්ලීම් වැඩි වැඩියි. කරුණාකර මදක් රැඳී සිට නැවත උත්සාහ කරන්න. | அதிக கோரிக்கைகள். சிறிது நேரம் காத்திருந்து மீண்டும் முயற்சிக்கவும். | confirmations/routes.py HTTP 429 | | |

## Notes for the reviewer

* `voice.gender.letter_female` and `voice.gender.letter_male` are the single letter shown on the voice and avatar switch (F and M in English).
* `voice.mode.gemini_live` keeps the product name and takes the model id as `{model}`; check that the Sinhala and Tamil text does not need a word around it.
* `chat.greeting`, `chat.guest`, `voice.greeting`, `voice.guest`, `voice.closing_question` and `voice.goodbye` are existing texts moved out of `app.js` and `backend/server.py`; they are listed so the same review covers them.
* `mode.*` replaces the English strings the backend sends as `mode` (for example `cloud multi agent (gemini)`). The page maps them, see `docs/patches/D_code_changes.md`.
