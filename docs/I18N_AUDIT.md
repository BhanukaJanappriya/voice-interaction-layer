# i18n audit

Written 2026-10-15 by Agent D (requirement R4: the language chosen on the first screen controls everything). Generated from `tests/test_i18n_hardcoded.py` run as a script with the regen flag plus a manual read of `frontend/` and the backend modules that talk to the visitor. Every finding below is also a line in `tests/i18n_allowlist.txt`, so the test only fails on new ones.

Severity: **high** the visitor sees or hears English in Sinhala or Tamil mode; **medium** rare path, developer-ish wording, or already translated but kept in code; **low** LLM prompt, English fallback that mirrors a key, brand name, internal label.
Status: **FIX** a patch is proposed in `docs/patches/D_i18n_keys.json` and `docs/patches/D_code_changes.md`; **MOVE** translated already, lives in a code table, should move to the json files; **OK** intentional.

## Summary

| Status | High | Medium | Low |
|-|-|-|-|
| FIX | 62 | 42 | 0 |
| MOVE | 0 | 225 | 0 |
| OK | 0 | 0 | 186 |

Scanner findings: 515 literals (one allowlist line can cover several, for example `*non-latin`). Frontend 117, backend 398.

## Findings that need a fix (FIX), by severity

| File:line | String | Proposed key | Severity | Note |
|-|-|-|-|-|
| `backend/agent.py:728` | Sorry, I lost my connection there. Could you ask that again? | `llm.lost_connection` | high | spoken and shown apology, English only |
| `backend/agent.py:766` | Sorry, I lost my connection there. Could you ask that again? | `llm.lost_connection` | high | spoken and shown apology, English only |
| `backend/auth/routes.py:83` | Too many incorrect attempts. This account is locked; try again in about | `auth.account_locked` | high | HTTP detail shown by the legacy PIN form |
| `backend/auth/routes.py:93` | Too many attempts from this device. Please wait a minute and try again. | `auth.device_limit` | high |  |
| `backend/auth/routes.py:129` | The phone number or PIN is incorrect. | `auth.bad_credentials` | high |  |
| `backend/livekit_transport/session.py:788` | No microphone audio arrived, so the conversation is ending. | `voice.no_mic_audio` | high | error event shown in the transcript and caption |
| `backend/llm_chain/chain.py:29` | Sorry, I lost my connection there. Could you ask that again? | `llm.lost_connection` | high | spoken and shown apology, English only |
| `backend/llm_chain/chain.py:30` | Sorry, I can't help with that request. | `llm.safety_blocked` | high | existing key llm.safety_blocked has the translations |
| `backend/llm_chain/chain.py:31` | Sorry, I can't reach my language service right now. Please try again in a moment. | `llm.unavailable` | high |  |
| `backend/otp/hub_backend.py:43` | your registered mobile number | `otp.destination_generic` | high | shown in the legacy form: We sent a code to your registered mobile number |
| `backend/otp/routes.py:46` | The code is incorrect or has expired. | `otp.bad_code` | high |  |
| `backend/otp/routes.py:65` | Too many incorrect codes. Please request a new code. | `otp.locked` | high |  |
| `backend/otp/routes.py:73` | A code was sent a moment ago. Please wait before asking for another. | `otp.too_soon` | high |  |
| `backend/otp/routes.py:75` | Too many attempts. Please try again later. | `otp.rate_limited` | high |  |
| `backend/otp/routes.py:85` | One time codes are not available right now. Please use your PIN. | `otp.unavailable` | high |  |
| `backend/voice_router/handover.py:15` | One moment, reconnecting. | `voice.reconnecting` | high | spoken notice on a tier change; handover.py already has NOTICE_KEY, the key and its translations are missing |
| `frontend/app.js:545` | Introduction | `chat.intro_meta` | high | meta label under the first bubble |
| `frontend/app.js:890` | Error: X | `error.prefix` | high | server error text; the server message itself is English (see backend notes) |
| `frontend/app.js:891` | Error: X | `error.prefix` | high | server error text; the server message itself is English (see backend notes) |
| `frontend/app.js:972` | No backend URL is configured. | `error.unreachable` | high | developer wording shown to the visitor |
| `frontend/app.js:980` | Microphone access denied or unavailable: X | `voice.mic_denied` | high | also leaks the browser error text |
| `frontend/app.js:1026` | Could not reach the backend. Is it running? | `error.unreachable` | high | developer wording shown to the visitor |
| `frontend/app.js:1043` | Connecting… | `loader.connecting` | high | existing key |
| `frontend/app.js:1055` | Tap anywhere to turn the sound on. | `voice.tap_sound` | high |  |
| `frontend/app.js:1157` | Start the conversation | `voice.mic.start` | high |  |
| `frontend/app.js:1161` | Unmute microphone | `voice.mic.unmute` | high |  |
| `frontend/app.js:1165` | Mute microphone | `voice.mic.mute` | high |  |
| `frontend/app.js:1249` | Male voice and avatar. Switch to female | `voice.gender.male` | high | both states are on one line |
| `frontend/app.js:1249` | Female voice and avatar. Switch to male | `voice.gender.female` | high | both states are on one line |
| `frontend/app.js:1274` | The male avatar is not installed; the voice has changed. | `voice.male_missing` | high |  |
| `frontend/app.js:1336` | Agent mode: - | `chat.mode_none` | high | also contains a dash character |
| `frontend/app.js:1341` | Agent mode: X | `chat.mode` | high | the value comes from the backend as English, map it with mode.* |
| `frontend/app.js:1384` | Error: could not reach the backend (X). | `error.unreachable` | high |  |
| `frontend/index.html:6` | text=SLT Mobitel Voice Assistant | `app.title` | high | document title stays English in si and ta |
| `frontend/index.html:55` | aria-label=Toggle dark and light theme | `a11y.theme_toggle` | high |  |
| `frontend/index.html:72` | text=Enable your microphone | `mic.title` | high |  |
| `frontend/index.html:73` | text=This assistant needs microphone access to hear you speak. Your browser will ask you to confirm right | `mic.body` | high |  |
| `frontend/index.html:74` | text=Allow microphone | `mic.allow` | high |  |
| `frontend/index.html:75` | text=Not now | `mic.not_now` | high |  |
| `frontend/index.html:111` | text=How would you like to talk today? | `select.tagline` | high |  |
| `frontend/index.html:117` | text=Start Chat | `select.chat` | high |  |
| `frontend/index.html:126` | text=Start Voice | `select.voice` | high |  |
| `frontend/index.html:133` | text=Change language | `nav.change_language` | high |  |
| `frontend/index.html:145` | text=Are you a new or existing user? | `user.tagline` | high |  |
| `frontend/index.html:149` | text=Existing user | `user.existing` | high |  |
| `frontend/index.html:150` | text=New user | `user.new` | high |  |
| `frontend/index.html:184` | text=Back | `nav.back` | high |  |
| `frontend/index.html:199` | aria-label=View conversation history | `voice.history` | high |  |
| `frontend/index.html:211` | aria-label=Log out | `voice.logout` | high |  |
| `frontend/index.html:223` | aria-label=Female voice and avatar. Switch to male | `voice.gender.female` | high | also rewritten by applyGenderUI() |
| `frontend/index.html:265` | aria-label=Toggle captions | `voice.captions` | high |  |
| `frontend/index.html:272` | aria-label=Start the conversation | `voice.mic.start` | high | also rewritten by the mic state code |
| `frontend/index.html:280` | aria-label=End the conversation | `voice.end` | high |  |
| `frontend/index.html:296` | text=Conversation history | `history.title` | high |  |
| `frontend/index.html:297` | aria-label=Close history | `history.close` | high |  |
| `frontend/index.html:305` | text=Your conversation will appear here as it happens. | `empty.transcript` | high | existing key, only wired in dashboard.js on a narrow screen |
| `frontend/index.html:314` | text=Text chat | `chat.title` | high |  |
| `frontend/index.html:319` | text=Type a message below to start chatting. | `chat.empty` | high |  |
| `frontend/index.html:322` | placeholder=Type a message… | `chat.placeholder` | high |  |
| `frontend/index.html:323` | text=Send | `chat.send` | high |  |
| `frontend/index.html:326` | text=Agent mode: - | `chat.mode_none` | high | also rewritten by setChatModeBadge() |
| `frontend/index.html:332` | text=Back | `nav.back` | high |  |
| `backend/agent.py:234` | Error: could not reach the local Ollama server. Is it running? | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:237` | Error: the local Ollama server took too long to respond. | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:242` | Error: Ollama server returned an HTTP error ( | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:245` | Error: could not get a response from Ollama ( | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:250` | Error: received an unreadable response from the Ollama server. | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:282` | Error: could not reach the local Ollama server. Is it running? | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:286` | Error: the local Ollama server took too long to respond. | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:293` | Error: Ollama server returned an HTTP error ( | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/agent.py:297` | Error: could not get a response from Ollama ( | `llm.local_unavailable` | medium | developer error text returned as the reply when the local model fails |
| `backend/auth/routes.py:154` | Not signed in. | `auth.not_signed_in` | medium |  |
| `backend/confirmations/routes.py:79` | Too many requests | `api.rate_limited` | medium | HTTP 429 detail |
| `backend/confirmations/routes.py:87` | Too many requests | `api.rate_limited` | medium | HTTP 429 detail |
| `backend/confirmations/routes.py:95` | Too many requests | `api.rate_limited` | medium | HTTP 429 detail |
| `backend/livekit_transport/tokens.py:185` | The LiveKit voice agent is not running right now. | `voice.agent_down` | medium | HTTP 503 detail; the page falls back silently |
| `backend/otp/routes.py:93` | One time code sign in is not enabled. | `otp.disabled` | medium |  |
| `frontend/app.js:246` | Continue | `legacy.continue` | medium | legacy PIN form |
| `frontend/app.js:246` | Send code | `legacy.send_code` | medium | legacy PIN form |
| `frontend/app.js:246` | Verify | `legacy.verify` | medium | legacy PIN form |
| `frontend/app.js:276` | Resend code in Xs | `legacy.resend_in` | medium | legacy PIN form |
| `frontend/app.js:276` | Resend code | `legacy.resend` | medium | legacy PIN form |
| `frontend/app.js:297` | Please enter a valid Sri Lankan landline number. | `verify.errors.phone_format` | medium | existing key |
| `frontend/app.js:299` | Too many wrong codes. Please request a new code. | `legacy.err.code_locked` | medium | legacy PIN form |
| `frontend/app.js:301` | Please wait X seconds before trying again. | `legacy.err.wait_seconds` | medium | legacy PIN form |
| `frontend/app.js:301` | Too many attempts. Please wait a moment and try again. | `legacy.err.too_many` | medium | legacy PIN form |
| `frontend/app.js:304` | One time codes are not available right now. Please use your PIN. | `legacy.err.otp_unavailable` | medium | legacy PIN form |
| `frontend/app.js:306` | That code is not correct or has expired. | `legacy.err.code_wrong` | medium | legacy PIN form |
| `frontend/app.js:331` | We sent a code to X. It is valid for X minutes. | `legacy.otp_sent` | medium | legacy PIN form |
| `frontend/app.js:394` | Too many incorrect attempts. This account is locked for about X minuteX. | `legacy.err.account_locked` | medium | legacy PIN form |
| `frontend/app.js:397` | Too many attempts from this device. Please wait a minute and try again. | `legacy.err.device_limit` | medium | legacy PIN form |
| `frontend/app.js:400` | Please enter a valid Sri Lankan landline number and a 4 to 6 digit PIN. | `legacy.err.phone_pin_format` | medium | legacy PIN form |
| `frontend/app.js:402` | That number and PIN do not match our records. | `legacy.err.mismatch` | medium | legacy PIN form |
| `frontend/index.html:158` | text=Your landline number | `legacy.phone_label` | medium | legacy PIN form, only shown when verify.js did not load |
| `frontend/index.html:160` | text=PIN | `legacy.pin_label` | medium | legacy PIN form |
| `frontend/index.html:161` | placeholder=4 to 6 digits | `legacy.pin_placeholder` | medium | legacy PIN form |
| `frontend/index.html:168` | text=Verification code | `legacy.otp_label` | medium | legacy PIN form |
| `frontend/index.html:169` | placeholder=6 digit code | `legacy.otp_placeholder` | medium | legacy PIN form |
| `frontend/index.html:170` | text=Resend code | `legacy.resend` | medium | legacy PIN form |
| `frontend/index.html:174` | text=Continue | `legacy.continue` | medium | legacy PIN form |
| `frontend/index.html:175` | text=Send me a one time code instead | `legacy.use_otp` | medium | legacy PIN form |
| `frontend/index.html:176` | text=Use my PIN instead | `legacy.use_pin` | medium | legacy PIN form |
| `frontend/index.html:177` | text=Use a different option | `legacy.use_other` | medium | legacy PIN form |
| `frontend/index.html:339` | text=SLT Digital Lab, voice interaction prototype | `footer.credit` | medium |  |

## Translated but kept in code (MOVE)

| File:line | String (English entry or first literal) | Severity | Note |
|-|-|-|-|
| `backend/access/messages.py:22` | I am sorry, I cannot help with account specific matters such as bills, balances, connection status, | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/access/messages.py:56` | Your verified session has ended, so I can no longer open your account details. Please verify again w | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:21` | Buy an extra data package | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:22` | Add a data bundle | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:23` | Subscribe to a package | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:24` | Log a fault | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:34` | {action}: {details}. Please say yes or tap Confirm within {seconds} seconds to go ahead, or say no t | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:35` | I cancelled that. Nothing was changed. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:36` | That request timed out, so I did not do it. Nothing was changed. Ask me again if you still want it. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:37` | I could not find a request waiting for your answer. Nothing was changed. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:38` | This feature is not switched on, so nothing was changed. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:39` | I can't make changes through this assistant yet, so nothing was changed. Please dial the hotline {ho | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:40` | The request was not completed. SLT replied: | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:41` | I could not reach SLT, so I cannot tell whether it went through. I did not try again. Please check y | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:42` | Your sign in has ended, so nothing was changed. Please verify again. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:43` | I am not allowed to do that, so nothing was changed. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:45` | I could not read the package list right now. Nothing was changed. Please try again later. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:46` | I could not find a package that matches. Nothing was changed. Please tell me the package name again. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:47` | Which package do you mean? {options} | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:48` | I cannot tell which kind of connection this is, so I cannot buy a package. Please dial {hotline}. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:49` | Which service is the fault with: broadband, voice, PEO TV or other? | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:50` | Which connection is it? Reply with the number: {options} | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:51` | What contact number should SLT call you on? Say skip if you do not want to give one. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:52` | In a few words, what is the problem? Say skip to leave it blank. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:53` | I could not find a service to log a fault for. Please dial {hotline}. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:54` | I stopped the fault report. Nothing was logged. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:55` | I did not understand that. Please reply with one of the numbers. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:56` | Checking the available packages | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:57` | Preparing your fault report | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/confirmations/texts.py:58` | Making the change | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/customer_service/messages.py:19` | To show your account details I need you to verify again. Please verify with a one time code and then | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/customer_service/messages.py:23` | I cannot reach your account details right now. Please try again later, or contact SLT directly: call | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/customer_service/messages.py:27` | I can only show account information here, I cannot make changes. To buy, activate, change or cancel | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/otp/sender.py:44` | SLT Mobitel verification code: {code}. Valid for {minutes} minutes. Do not share it with anyone. | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/otp/sender.py:49` | Your SLT Mobitel verification code | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/server.py:436` | You're welcome! Is there anything else I can help you with, or shall I end our conversation here? | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/server.py:441` | Thank you for using the SLT Mobitel voice assistant. Goodbye, and have a great day! | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/server.py:493` | Welcome to SLT Mobitel, {name}! I'm your voice assistant, and I understand English, Sinhala, and Tam | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:19` | I could not find that in the SLT information I have. Please call {hotline} (toll free from SLT lines | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:27` | The SLT information service is temporarily unavailable. Please try again shortly, or contact SLT: ca | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:36` | Your verified session has ended, so I can no longer open your account details. Please verify again w | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:44` | There are {count} more. Would you like me to read them? | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:49` | Searching the SLT information | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:54` | Checking your balance | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:57` | Checking your bills | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:60` | Checking your account | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:63` | Checking your faults | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/messages.py:69` | Checking your usage | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `backend/tool_agent/voice.py:23` | and {c} cents | medium | English entry of a three language table kept in code; si and ta exist beside it; should move into backend/i18n |
| `frontend/app.js:520` | Welcome to SLT Mobitel, X! I'm your voice assistant, and I understand | medium | CHAT_GREETINGS table in code; si and ta text exist but outside the json files |
| `frontend/app.js:521` | English, Sinhala, and Tamil, so just start speaking whenever you're ready. | medium | second half of the same greeting |

## Sinhala or Tamil literals in code (one row per file)

| File | Literals | Status | Note |
|-|-|-|-|
| `backend/access/classifier.py` | 6 | OK | Sinhala or Tamil keyword lexicon used to recognise what the visitor said, not output |
| `backend/access/messages.py` | 4 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/agent.py` | 4 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/confirmations/api.py` | 2 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/confirmations/intents.py` | 43 | OK | Sinhala or Tamil keyword lexicon used to recognise what the visitor said, not output |
| `backend/confirmations/texts.py` | 82 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/customer_service/intent.py` | 31 | OK | Sinhala or Tamil keyword lexicon used to recognise what the visitor said, not output |
| `backend/otp/sender.py` | 4 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/server.py` | 13 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/tool_agent/customer.py` | 2 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/tool_agent/knowledge.py` | 16 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/tool_agent/messages.py` | 22 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/tool_agent/scrub.py` | 1 | OK | Sinhala or Tamil keyword lexicon used to recognise what the visitor said, not output |
| `backend/tool_agent/voice.py` | 13 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `backend/voice_text/normalizer.py` | 2 | OK | Sinhala or Tamil keyword lexicon used to recognise what the visitor said, not output |
| `frontend/app.js` | 6 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |
| `frontend/ui/landing/landing.js` | 6 | MOVE | three language table kept in code; translated but outside backend/i18n or frontend/i18n |

## Intentional or internal (OK)

| File:line | String | Note |
|-|-|-|
| `backend/access/prompt.py:28` | a verified customer | LLM prompt or internal label, never shown to the visitor |
| `backend/access/prompt.py:30` | ACCESS LEVEL: verified. The caller is | LLM prompt or internal label, never shown to the visitor |
| `backend/access/prompt.py:30` | ), confirmed by a one time code. You may discuss this customer's own account data that too | LLM prompt or internal label, never shown to the visitor |
| `backend/access/prompt.py:40` | ACCESS LEVEL: guest. The caller is not verified. You may chat about general topics and giv | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:78` | You are a concise voice assistant. Answer only what the user actually asked, nothing more  | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:96` | Always reply in English. | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:139` | The user's name is | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:139` | . Address them by name naturally, for example when greeting or thanking them, but not in e | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:144` | The user has not shared their name. If you address them directly, call them | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:241` | ' was not found on the Ollama server. | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:291` | ' was not found on the Ollama server. | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:333` | the request was rejected (often an invalid API key format or a bad parameter) | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:334` | the API key is invalid or was revoked; create a new one in AI Studio | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:335` | the project's prepaid credits are used up; top up billing in AI Studio | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:336` | the key is not allowed to use this API or model (API disabled, or key restrictions) | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:338` | the rate limit or quota was hit; wait, or raise the quota in AI Studio | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:339` | Google's service had an internal error; usually temporary | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:340` | Google's service is overloaded or unavailable; usually temporary | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:371` | network problem reaching Google | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:493` | You are the reasoning stage of a voice assistant. In two or three short sentences, identif | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:500` | You are the response stage of a voice assistant. You will be given the user's message, opt | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:587` | Reply with one word. | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:692` | access policy (canned reply) | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:701` | SLT hub (fixed reply) | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:759` | cloud multi agent ( | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:771` | local single agent (Llama via Ollama) | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:825` | Information from the SLT systems (the only source for SLT facts): | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:851` | account data unavailable (fixed reply) | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:883` | SLT account data for this customer, from the SLT system: | LLM prompt or internal label, never shown to the visitor |
| `backend/agent.py:887` | + SLT account data | LLM prompt or internal label, never shown to the visitor |
| `backend/auth/dependencies.py:107` | Admin sign in required. | admin portal stays English |
| `backend/auth/routes.py:163` | The username or password is incorrect. | admin portal stays English |
| `backend/customer_service/messages.py:78` | The customer is verified and the SLT ACCOUNT DATA block below belongs to them. | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/messages.py:79` | Answer only from that block. If the figure asked for is not in it, say you do not have it  | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/messages.py:81` | Give only what was asked, in short spoken sentences, and say amounts with their unit. | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/messages.py:82` | Never read out or repeat the telephone number, account numbers, tokens or codes. | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/messages.py:83` | You can only read account information. You cannot buy, activate, change, add, subscribe or | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/messages.py:88` | The customer asked for a change. Say plainly that changes cannot be made here and that the | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/render.py:14` | SLT ACCOUNT DATA of the verified customer (read only, from the SLT systems): | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/render.py:16` | (no data returned) | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/render.py:104` | tool reported an error | LLM prompt or internal label, never shown to the visitor |
| `backend/customer_service/tools.py:60` | is not on the read only allow list | LLM prompt or internal label, never shown to the visitor |
| `backend/llm_chain/chain.py:32` | no API key or credentials are configured | diagnostic reason for logs and /health |
| `backend/llm_chain/chain.py:122` | the provider returned no text | diagnostic reason for logs and /health |
| `backend/otp/sender.py:275` | is not one of console, email, sms | configuration error |
| `backend/server.py:357` | SLT MCP hub knowledge base | internal label or speech to text noise filter, not output |
| `backend/server.py:400` | thanks for watching | internal label or speech to text noise filter, not output |
| `backend/server.py:888` | Invalid start message: | developer error on a malformed start message |
| `backend/tool_agent/core.py:141` | Not available for this question: | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/customer.py:225` | the customer's current | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/customer.py:225` | , so packages could not be compared | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/customer.py:235` | the package comparison, the comparison service did not answer | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/customer.py:246` | + SLT account data | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/knowledge.py:63` | fibre FTTH broadband packages | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:64` | broadband internet packages | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:65` | LTE 4G packages | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:66` | PEO TV packages channels | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:67` | voice telephone plans | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:68` | data add-on package | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:70` | package price monthly rental | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:75` | international calls IDD | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/knowledge.py:126` | SLT INFORMATION (excerpts from the SLT knowledge base): | retrieval query expansion keywords, matched against the English knowledge base |
| `backend/tool_agent/messages.py:134` | Speak in short sentences. Copy every number, price, date and name exactly as given, and sa | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:143` | The only contact details you may give are: | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:144` | (prepaid). Never write any other telephone number, web address, e-mail address or app name | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:151` | Answer ONLY from the SLT excerpts below. Copy numbers, prices, package names and dates exa | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:159` | This message is conversation, not an SLT question. Do NOT state any SLT specific facts fro | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:168` | The customer is verified and the SLT ACCOUNT DATA block below belongs to them. | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:169` | Answer only from that block. If the figure asked for is not in it, say you do not have it  | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:171` | Never read out or repeat the telephone number, account numbers, tokens or codes. | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:173` | You can only read account information. You cannot buy, activate, change, add, subscribe or | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:177` | Not available for this question: | LLM prompt or internal label, never shown to the visitor |
| `backend/tool_agent/messages.py:177` | . Say so and do not make it up. | LLM prompt or internal label, never shown to the visitor |
| `frontend/index.html:93` | alt=SLT Mobitel | brand name, the same in every language |
| `frontend/index.html:96` | text=Choose your language | the language screen is trilingual by design; add lang attributes to the two alternates |
| `frontend/index.html:101` | text=English | language name in its own script, shown before a language is chosen |
| `frontend/index.html:110` | alt=SLT Mobitel | brand name, the same in every language |
| `frontend/index.html:144` | alt=SLT Mobitel | brand name, the same in every language |
| `frontend/index.html:229` | alt=SLT Mobitel | brand name, the same in every language |
| `frontend/index.html:313` | alt=SLT Mobitel | brand name, the same in every language |
| `frontend/ui/confirm/confirm.js:23` | This request timed out. Nothing was changed. | English fallback table, mirrors the existing keys |
| `frontend/ui/confirm/confirm.js:24` | Cancelled. Nothing was changed. | English fallback table, mirrors the existing keys |
| `frontend/ui/confirm/confirm.js:25` | This request is no longer valid. Nothing was changed. | English fallback table, mirrors the existing keys |
| `frontend/ui/confirm/confirm.js:26` | I could not tell whether it went through. Please check your account before trying again. | English fallback table, mirrors the existing keys |
| `frontend/ui/confirm/confirm.js:28` | Check the details, then confirm to go ahead. | English fallback table, mirrors the existing keys |
| `frontend/ui/dashboard/dashboard.js:13` | Voice and avatar | English fallback FB, mirrors the existing keys |
| `frontend/ui/dashboard/dashboard.js:13` | Log out | English fallback FB, mirrors the existing keys |
| `frontend/ui/dashboard/dashboard.js:13` | Intro sound | English fallback FB, mirrors the existing keys |
| `frontend/ui/dashboard/dashboard.js:16` | Your conversation will appear here as it happens. | English fallback EMPTY, mirrors the existing key |
| `frontend/ui/landing/landing.js:29` | Welcome to SLT Mobitel | English fallback when the json did not load, mirrors the existing keys |
| `frontend/ui/landing/landing.js:29` | Your voice assistant in English, Sinhala and Tamil | English fallback, mirrors the existing keys |
| `frontend/ui/landing/landing.js:29` | Sri Lanka's No.1 Connection | English fallback, mirrors the existing keys |
| `frontend/ui/rating/rating.js:22` | How was your experience? | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:23` | Tell us how this chat went. | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:24` | Tell us how this voice call went. | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:27` | Poor | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:28` | Fair | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:29` | Good | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:30` | Very good | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:31` | Excellent | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:32` | Choose a star | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:35` | Thank you for your feedback | English fallback table, mirrors the existing keys |
| `frontend/ui/rating/rating.js:36` | Could not send, thank you anyway | English fallback table, mirrors the existing keys |

## Gaps the scanner cannot see (manual findings)

| Area | Finding | Fix |
|-|-|-|
| Document title | `document.title` stays English after a language is chosen; only `<html lang>` is set | key `app.title`, `i18n.js` hunk (D_code_changes section 3) |
| Language screen | the two alternate headings have no `lang` attribute, so screen readers and fonts treat Sinhala and Tamil as English | `lang` attributes (section 4) |
| Mode text | the backend sends English `mode` strings (for example `cloud multi agent (gemini)`) and the page shows them raw in bubbles, the chat badge and the dashboard chip | `modeLabel()` plus keys `mode.*`, `voice.mode.*` (section 5, hunk A9) |
| Tier notice | `voice.reconnecting` is referenced by `voice_router/handover.py` but the key and its translations did not exist | added in both json patches |
| Server error events | `{"type": "error", "message": ...}` carries developer English, wrapped by the page | `error.prefix`, backend keys for the two visitor visible ones |
| Numbers and dates | nothing is formatted per language; countdowns and counts are built by hand | `frontend/i18n/format.js` and the call site table (section 2) |
| Duplicate sources | `otp/sender.py` tables against `otp.sms_body`, and several `BUILTIN` tables, can drift | MOVE rows above |
| Landing greeting | on a first visit no language is chosen yet, so it follows the browser language | by design |
| Manifest | `manifest.webmanifest` name and description are English only | leave, note in KNOWN_LIMITATIONS |
| Admin portal | English by decision | none |
| Fonts | Noto Sans Sinhala and Tamil are loaded from Google Fonts in `index.html`; the font stack does not put them first and there is no offline copy | section 8 and `D_third_party.md` |
