# Known Limitations

An honest account of what does not fully work yet, why, and what would
actually fix it, so this can be presented accurately rather than oversold.

## The 3D avatar's mouth is text paced, not audio driven lip sync

**Status: a genuine, working approximation, not true lip sync.**

`frontend/bot3d.js` renders a real 3D, WebGL lit face (Three.js) whose
mouth animates while the assistant is speaking. It is driven by the
actual reply text: each word is timed at a natural conversational rate
(165 words per minute), then split further into consonant and vowel
clusters (`wordVisemeSequence()`, e.g. "hello" -> "h", "e", "ll", "o"),
each given a mouth shape guessed from its vowels (rounded for o/u, wide
for a/e, mid for i, mostly closed for a consonant run) and stepped
through within that word's time slice, capped at four clusters per word
so an unusual long word cannot fragment into unreadably fast flicker.
This is a step up from an earlier version that guessed one shape for an
entire word: the mouth now changes shape several times within a single
word, roughly following its syllable structure, which is genuinely tied
to what the bot is saying and to its approximate pronunciation, not a
fixed loop or a single per word guess.

It is not audio driven viseme animation, and cannot be, without a bigger
change: the synthesized speech audio plays on the backend machine's own
speakers and never reaches the browser (see ARCHITECTURE.md's data flow
diagram), so there is no audio waveform in the page to analyze or sync
against. The word timing is an estimate, not the TTS engine's actual
per-word timing, so it will drift out of sync with the real audio over a
long reply, especially against a cloud TTS voice whose pacing differs
from the 165 words per minute assumption.

**Real fix:** stream the synthesized audio to the browser (already listed
as a suggested next step in README.md for a different reason, in-page
playback) and either use the Web Audio API's analyser node to drive mouth
shape from the actual waveform, or have the TTS provider return word or
phoneme level timing (several cloud TTS APIs, including Google Cloud's,
can return timepoints) and drive the mouth from that instead of an
estimate.

## The agent's reply streams in real time on the voice path only

**Status: genuinely real time on the voice path, cloud and local; not on the text chat screen.**

The WebSocket voice pipeline streams the agent's reply as it is generated
and speaks it at the same time: each sentence goes to text to speech the
moment the agent finishes writing it (`SentenceChunker` and
`speak_stream()` in `backend/tts.py`, wired up by `_speak_streamed_reply()`
in `backend/server.py`), so the first sentence is already playing while
the rest is still being written. Both the local Llama path and the cloud
path stream now; the cloud path used to run a separate reasoning call to
completion first and then return the whole reply as one piece, which is
now optional (`CLOUD_REASONING_STAGE`, off by default).

The text chat screen's `POST /chat` endpoint is not streamed; it is a
plain request/response call that waits for the full reply. Streaming a
plain HTTP POST would need Server Sent Events or a similar mechanism on
both ends, which was not built since the voice path already demonstrates
real streaming end to end.

One side effect worth knowing: to get the first words out sooner, the
very first piece of a reply may be cut at a comma (for example the
assistant says "Nimal," and then carries on with the rest of the
sentence). Only the first piece is cut that way, so the rest of the reply
keeps normal sentence rhythm.

## WebGL/Three.js is required for the 3D face; a flat 2D face is the fallback

**Status: handled, not a gap.**

`bot3d.js` feature detects WebGL and whether the Three.js script actually
loaded before doing anything else; if either is unavailable, it sets
`window.bot3d.available = false` and leaves the original flat CSS/HTML
avatar (blinking eyes, a mouth that scales per state) fully in control,
rather than showing a broken canvas or crashing. This was exercised for
real during development: the Three.js CDN URL initially pointed at a path
that returned a 503, and the flat avatar kept working correctly the whole
time with a plain console message explaining why the 3D layer was not
active.

## Sinhala and Tamil voice quality (text to speech)

**Status: solved for the common case, via a free path with no credentials.**

This development machine has exactly two installed SAPI5 voices (checked
directly, not assumed): "Microsoft David" (male, English US) and
"Microsoft Zira" (female, English US). Neither Sinhala nor Tamil have any
local voice available at all, and that has not changed.

What has changed: `backend/tts.py` now tries edge-tts before falling back
to that local voice. edge-tts is an unofficial but very widely used
library that calls Microsoft Edge's own text to speech backend (the same
neural voice catalog Azure Cognitive Services uses) with no API key,
account, or credentials at all, only internet access. Verified live, not
assumed: real Sinhala (`si-LK-SameeraNeural`/`si-LK-ThiliniNeural`) and
Tamil (`ta-IN-ValluvarNeural`/`ta-IN-PallaviNeural`) audio was generated
and played through the full `speak_text()` path with no cloud credentials
configured at all, male and female voices both, alongside English. The
spoken English notice this section used to describe as the normal outcome
for these two languages is now the rare fallback: only if edge-tts's own
call fails too (no internet, or Microsoft's service being unreachable) is
it ever reached, not the default path with no credentials configured.

When real Google Cloud credentials are configured, `_speak_cloud_google()`
now asks for explicit Chirp3-HD voices by name (`en-US-Chirp3-HD-Kore`/
`-Puck` for English, `ta-IN-Chirp3-HD-Kore`/`-Puck` for Tamil) rather than
letting Google pick any voice matching a requested gender, which could
land on any tier in its catalog including the older, audibly synthetic
"Standard" voices. Chirp3-HD is confirmed, live against a real account's
own voice list, to be Google's newest and most natural tier. Sinhala has
no entry in that map at all: a live `list_voices(language_code='si-LK')`
call against the real API returned zero results, not a transient error,
so `_speak_cloud_google()` now skips the network call for Sinhala
entirely and falls straight through to edge-tts, rather than making a
request that could only fail.

The configured cloud provider (Google Cloud TTS Chirp3-HD, where
available, or Azure Neural TTS) is still tried first when real
credentials are set, and remains the higher quality, quota backed option
for production use; edge-tts is what makes a genuinely natural voice
available with zero setup, not a replacement for a real account when one
is warranted.

**Known caveat:** edge-tts calls an undocumented, unofficial endpoint of
Microsoft's Edge browser service, not a published, versioned API with a
support contract. It could change or be restricted without notice; the
cloud provider adapters remain the durable, supported path for anything
beyond a demo or prototype.

## Sinhala and Tamil transcription accuracy (speech to text)

**Status: Tamil works locally with the larger model; Sinhala does not work
locally at all and needs Google Cloud Speech-to-Text, which is not enabled.**

Measured on 2026-10-02 with spoken test questions (six each, edge-tts
voices, language hint set as the app does):

- **Tamil:** `base` made 21.1% character errors, `small` 6.7%. The app
  rechecks a final transcript the main model is unsure of with `small`
  (STT_ACCURATE_MODEL), so Tamil is usable locally.
- **Sinhala:** `base` returned romanized nonsense (161% character errors)
  and `small` produced garbage (246%). Earlier notes called local Sinhala
  "usable for a rough demo"; that was wrong. The adapter for Google Cloud
  Speech-to-Text (`si-LK`, `ta-IN`) exists in `backend/stt.py` and is tried
  first for those languages, but the API is not switched on in the Google
  project of the configured credentials: every call answers 403 "Cloud
  Speech-to-Text API has not been used in project ... before or it is
  disabled". Enabling it at the activation link in that error message
  (console.developers.google.com, API "Cloud Speech-to-Text API") is the
  fix and needs no code change. It has therefore never run against a live
  Sinhala utterance. Until then, a Sinhala speaker gets a wrong or empty
  transcript; the text chat screen is unaffected.
- After a cloud failure the app skips the cloud for 5 minutes instead of
  trying it for every transcript, so a disabled API does not slow
  conversations down.

The spoken test sets are synthetic (text to speech voices), so real
microphones, real Sri Lankan accents and real rooms will be harder than the
numbers in ARCHITECTURE.md suggest.

## "Streaming" transcription is chunk based, not natively streaming

**Status: a real, working, but honestly limited form of streaming.**

faster-whisper has no native partial decoding API: every call transcribes
whatever audio buffer it is given, start to finish. `transcribe_stream()`
approximates streaming by re-running transcription on the growing speech
buffer every few chunks (about every 1.5 seconds of new speech) and finishing
with one pass over the whole utterance once trailing silence is detected.
This has been verified to work end to end, including a real test where a
synthesized sentence was fed through the pipeline in 0.5 second chunks and
produced growing partial transcripts that converged to an exact final
transcript. It genuinely reduces perceived latency versus the prototype's
record-then-transcribe design, and the frontend does show text while the
user is still talking. It is not, however, the same as a true streaming
ASR API (Google Cloud Speech to Text's streaming mode, for example, decodes
incrementally at the model level).

**Real fix:** for English or any language with cloud speech configured, a
true streaming recognizer could replace the local re-transcription
approach; this was judged out of scope to build for this pass given the
timeline, since the chunk based approach already satisfies the "feels live"
requirement in practice.

## Checking why Gemini is not answering

`GET /health` now reports `cloud_llm_model` and `cloud_llm_last_error`,
the reason the cloud call last failed in plain words
(`_GEMINI_ERROR_REASONS` in `backend/agent.py`): 401 invalid key, 402 no
credit left, 403 key not allowed, 404 model not available to this key,
429 rate limit or quota, 5xx Google side outage, no code at all for a
network problem. The same text is in the server log.

Diagnosed live on 2026-09-24 and again on 2026-10-01: the key itself is
valid (listing models succeeds, 61 visible), and pinned names such as
`gemini-2.5-flash` and `gemini-2.5-flash-lite` are retired for newer
accounts (404). The default is now the alias `gemini-flash-lite-latest`
(`GEMINI_MODEL`), which always points at a current model, so a retirement
cannot break it the same way again. Every model, the alias included,
answers 402 "prepayment credits are depleted", because the project behind
the key has no credit left. That cannot be fixed in code: top up billing at
https://ai.studio/projects, or use a key from a project that has credit or
a free tier. The cloud path then resumes after an app restart (a failure
pauses it for 5 minutes, and a restart clears that).

## The cloud multi agent path has not been run against a live account

**Status: built and reviewed, not live tested.**

No Gemini or OpenAI API key was available in this environment. The
`GeminiAgent`, `GptAgent`, and `Orchestrator` classes in `backend/agent.py`
are implemented, and the fallback direction (cloud unavailable -> local
agent alone) has been proven repeatedly, including through an automated
end to end WebSocket test that confirmed the correct mode is logged and
sent to the frontend. The forward direction (cloud actually configured and
reachable, three role collaboration actually executing) has not been
exercised live. Once a key is added to `.env`, run
`backend/agent.py`'s standalone test mode first to confirm the cloud path
directly before trusting it in a full demo.

## Spectral gating noise reduction depends on a scipy version pin

**Status: solved, but the cause is worth knowing. Since 2026-10-02 the
filter is off by default** (`STT_DENOISE=false`): on spoken test clips it
raised Whisper's word errors from 3.3% to 11.0%, so transcription no longer
runs it unless asked to. `denoise_audio()` and its pin remain for anyone who
switches it on.

The newest scipy release available at the time of building this (1.18.1)
would not import on this machine: `ImportError: DLL load failed while
importing _batched_linalg: An Application Control policy has blocked this
file.` This is a Windows Application Control policy on this specific
machine refusing to load a compiled extension, not a bug in this project's
code. scipy 1.13.1 (which `noisereduce`, used for spectral gating noise
reduction in `backend/vad.py`, depends on) does not trigger the same
policy and works correctly; `requirements.txt` pins to that version with a
comment explaining why. `denoise_audio()` also degrades gracefully (falls
back to unprocessed audio) if this import ever fails again on a different
machine, so a future environment with a stricter or looser policy will not
crash the pipeline either way, just possibly skip this one noise cleanup
step.

## Browser microphone permission cannot be granted by automation

**Status: a testing constraint, not a product limitation.**

The voice screen now shows an explainer modal first, then relies on the
browser's own native permission prompt (triggered by `getUserMedia()`
inside `beginVoiceSession()`), which lives outside the page's own DOM and
cannot be driven or pre-answered by browser automation. This is a
limitation of automated testing, not of the product: a real user sees and
can answer that native prompt immediately after tapping "Allow
microphone" in the in-page explainer. In this project's own automated
verification, the test browser already had microphone access granted
from a prior session, so the fast path (skip the explainer, introduce
itself, and connect immediately) was exercised end to end, including a
real WebSocket connection to the backend confirmed in its own logs; the
first-time, permission-not-yet-granted path was verified by reading the
code and modal markup rather than by a live click-through, since nothing
in this environment can grant that native prompt.

## Ending the conversation on "thank you" is a keyword match, not real intent understanding

**Status: works for the common phrasing, not exhaustive.**

`backend/server.py`'s `_sounds_like_closing_signal()` and `_sounds_negative()`
decide when the assistant offers to end the conversation, and when a
follow up utterance confirms that, by matching a fixed, short list of
English phrases ("thank you", "that's all", "nothing else", ...) plus a
couple of common Sinhala (ස්තුතියි) and Tamil (நன்றி) words, the same
plain keyword matching approach `_looks_like_transcription_hallucination()`
already uses just above it in the same file, not an LLM based intent
classification. A real "thank you for explaining, but I have one more
question" is handled correctly (the closing offer is dropped once the
next utterance turns out not to confirm it), but a satisfied phrase this
list does not happen to include, especially in Sinhala or Tamil phrasing
beyond the one word each covered here, will simply not trigger the
closing flow; the conversation keeps going normally in that case rather
than ending awkwardly early, so this list being incomplete costs a missed
convenience, not a broken conversation.

**Real fix:** a small classification call (even a fast local model) to
judge "does this sound like the user is done," or a longer, reviewed
phrase list per language, if this is taken beyond a demo.

## No conversation memory across turns

Unchanged from the prototype: each turn is an independent request to the
active chat agent. Both the local and cloud paths currently discard prior
turns, so a follow up like "how fast can it be?" loses what "it" referred
to. The user's name is the one exception: it is kept for the whole
session and given to the agent on every turn.

## The Gemini key currently has no credit left

**Status: blocked on billing, not on code.**

As of 2026-09-24 every Gemini request with the configured key returns
`429 Your prepayment credits are depleted`, whatever the model name. The
Orchestrator falls back to local Llama, and its circuit breaker (see
`_CLOUD_RETRY_AFTER_SECONDS` in `backend/agent.py`) now skips the cloud
for 5 minutes after a failure, so a dead key no longer adds a failing
network call to every turn. The server also probes the cloud once at
startup, so even the first question skips it. Topping up the credits in
AI Studio is the only fix; no code change is needed, the cloud path picks
up again on its own once a probe succeeds.

## A turn ends after about 1 second of silence

`SILENCE_CHUNKS_TO_FINALIZE` in `backend/stt.py` was lowered from 1.5s to
1s of trailing silence, since that wait comes before every single reply.
Someone who pauses for longer than a second mid sentence will be cut off
and answered early; raising it back to 3 chunks trades that for a slower
reply.

## Existing users are verified by landline number and PIN

**Status: built and tested end to end (automated and through the real
app); the PIN is the weak point.**

"Existing user" used to be a typed name kept in the browser. It is now a
check against the customer database (`backend/auth`): the landline number
is validated and normalized to E.164, a PIN set at registration confirms
it, and the voice session and `POST /chat` take the name from the verified
session on the server. What this does not solve:

- **A 4 to 6 digit PIN is guessable on its own.** Five wrong PINs lock the
  account for 15 minutes and each client address gets 10 attempts per
  minute, which makes guessing slow, not impossible: someone patient could
  try about 480 PINs a day per account. A landline cannot receive an SMS
  code; a voice call one time code would need a telephony provider.
- **The lockout answer reveals that a number is registered.** A wrong
  number and a wrong PIN get the same answer, but only a registered number
  can become "locked" after five tries.
- **The rate limit lives in memory**, per process, and resets on restart.
  Behind a proxy, uvicorn needs `--proxy-headers` and
  `--forwarded-allow-ips` or every user shares the proxy's address.
- **Logout clears the cookie, not the token.** Sessions are stateless JWTs
  (60 minutes); a copied token keeps working until it expires.
- **No self registration.** Users are added with
  `python -m backend.auth.manage_users` or the demo seed; there is no sign
  up screen or user management page in the admin portal.
- The rest of the earlier behavior is unchanged: the introduction, replies
  and closing lines are in the language picked on the first screen
  (`_GREETINGS` in `backend/server.py`, `_REPLY_LANGUAGES` in
  `backend/agent.py`). A Guest can still say "my name is ..." to be called
  that (`_extract_user_name`, English only, ignores "I'm ..."); a verified
  user keeps the name on their account.

**Local Llama cannot really answer in Sinhala or Tamil.** Tested on
2026-09-24 with llama3.2 (3B): asked in Sinhala, it produced Sinhala
script that is not meaningful Sinhala; asked in Tamil, it produced
grammatical Tamil with a factually unrelated answer. The RAG evaluation on
2026-10-01 confirmed it with the right context in hand: 33% of expected
facts for Sinhala and 0% for Tamil, against 88% for English, while
retrieval itself found the right source for every Sinhala and Tamil
question. Gemini handles both languages properly, so Sinhala and Tamil
replies need a working Gemini key (see the credit issue above), or a local
model with real Sinhala and Tamil support set as `LOCAL_LLM_MODEL`.
Sinhala is spoken by edge-tts (Google has no Sinhala voice), which took
2.5 to 9 seconds per sentence over the network here, so the Guest version
of the Sinhala introduction is synthesized at server startup and cached.

## Knowledge base answers can go stale, and come from two official sites

**Status: working and measured (docs/RAG_EVAL.md); freshness depends on
rebuilding.**

- **Prices change.** The index is a snapshot from the last
  `python -m backend.knowledge.build`. Every chunk carries its source URL
  and retrieval date, and the assistant is told to say prices can change
  and to point to www.sltmobitel.lk or 1717, but it will quote the
  snapshot's price until the index is rebuilt. Nothing rebuilds it on a
  schedule.
- **Two sites, not one.** The brief named www.sltmobitel.lk, but that site
  loads every package table and price in the browser from a separate API
  (apphub.mobitel.lk) that needs a client id issued to the website; the
  crawler does not call it, and Playwright, which could render those
  pages, is not installed. The owner approved also crawling
  www.mobitel.lk, the same company's older, server rendered official site,
  which has the package tables. Measured: with the brief's site alone the
  right source is in the top 4 for 31% of test questions and there are no
  prices at all; with both, 97%. If www.mobitel.lk is retired, package
  coverage goes with it until the Playwright route is built.
- **Broad questions get thin answers.** "What data packages do you
  offer?" matches the site's generic category tiles better than any one
  package table, so the reply is general ("Fibre, 4G Home and roaming
  options") rather than a list of plans. Specific questions ("How much is
  the 7 day YouTube Combo?") get exact prices.
- **The local model still misses facts that are in front of it.** In the
  final evaluation 88% of the expected facts were in the notes the model
  received, and 79% appeared in its English answers (6% without notes): a
  3B model sometimes answers around a number or code instead of copying it
  ("since 1994" when the notes said 163 years). The rules reduce invention a
  lot, not to zero, and a larger or cloud model would close most of the gap.
- **Contact numbers can come out as the wrong official number.** The crawled
  contact pages hold several (customer service 1717, roaming hotline, head
  office menus). A keyword boost on chunk titles makes the customer service
  entry win for "hotline number", but other phrasings may surface another
  genuine number rather than an invented one.
- **Small talk sometimes carries irrelevant notes.** "What time is it" and
  "play some music" score just above the 0.53 threshold against opening
  hours pages; the strict prompt then says it has no such detail, which is
  harmless, but it is not the friendly general answer.
- **General answers are the model's own, not checked.** Outside SLT
  Mobitel facts the assistant answers from the model's general knowledge
  (a change made on 2026-10-01 at the owner's request, so it can hold a
  normal conversation). Nothing verifies those answers: the local 3B
  llama3.2 is right on common knowledge in the spot checks (capital cities,
  how 5G works, advice, programming resources) but can be confidently wrong
  on details, and the prompt only asks it to say so when unsure. The line
  between "SLT Mobitel specific" and "general" is the model's judgement,
  not code: an SLT Mobitel question the knowledge base does not cover
  should get "I do not have that detail" (it did in the spot checks), but a
  small model could sometimes answer it from general knowledge instead.
- **Other operators.** A question about another operator's package
  retrieves SLT Mobitel's own package context (it scores above the
  threshold); the prompt tells the model not to state other operators'
  prices or packages and to suggest that operator's own website, which it
  did in the spot checks, so the risk is an SLT Mobitel flavoured answer to
  a Dialog question, not invented Dialog prices.
- **The score threshold is tight.** 0.53 keeps all 18 small talk test
  phrases out (the highest scored 0.524) and lets real questions in, except
  one Tamil question at 0.492. New phrasings will land on either side of it
  now and then.
- **The cloud path's use of the context is not live tested**, for the same
  reason as the rest of the cloud path (no working Gemini key); unit tests
  confirm the context reaches the composer prompt.

## Grounded replies start speaking about 0.3 seconds later

**Status: measured, accepted for accuracy.**

First audio after the final transcript went from a median of 0.81 s to
1.11 s on the local Llama path (ARCHITECTURE.md has the table); the first
knowledge base design cost 0.7 s. Retrieval itself is 148 to 176 ms inside
the running server, slightly over the 150 ms target (75 ms p50 on its own);
the rest is the 3B model reading the notes (time to first token 180 ms
without, 321 ms with) and saying a full sentence instead of a short
invented one. A faster model, the cloud path, or a cached prompt prefix
would be the real fix.

Whisper also used to hear "Mobitel" as "Mobital" or "Mobilo", which hurt
retrieval; `STT_HOTWORDS` (default "SLT Mobitel") fixed every case in the
test questions, but an unusual accent or noisy room can still produce
other spellings. The hotword also makes Whisper write "SLT Mobitel" for
audio with no speech, which is why Whisper's speech filter is on.

The larger model recheck adds about a second to the turns it handles, about
19% of the synthetic test clips; real speech may trigger it more often.
`STT_ACCURATE_MODEL=` (empty) turns it off, and the first start downloads
the `small` model (about 460 MB).

## External chat agents: built and tested locally, not against a real one

**Status: built and tested against local stub servers (JSON, SSE, errors,
SSRF refusals, size cap, timeouts), plus the admin page in Chrome.**

No real third party agent over HTTPS has been registered: TLS certificate
checking with the connection pinning used for SSRF protection is covered
by reasoning and code review, not a live test. DNS resolution has no
timeout of its own, so a hanging DNS server can delay a turn beyond the
agent's configured timeout. An agent whose host does not resolve yet
cannot be saved (the address check runs at save time). The admin page was
checked at desktop width in both themes, not on a phone.

## MCP server: built and tested with the official SDK client, not with desktop MCP apps

**Status: built and tested end to end with the official Python SDK client
(token discovery, tools, 401/403, revocation, audit).**

It has not been tried from Claude Desktop, MCP Inspector or other MCP
apps. Many of those expect an interactive authorization code flow with
dynamic client registration; this server deliberately offers only the
client credentials grant for machine clients (docs/IMPLEMENTATION_PLAN.md,
ADR 6), so those apps need a static bearer header or client credentials
support. `/oauth/token` has no rate limit beyond bcrypt's own cost. The
SDK's Host and Origin checks only accept loopback and the host in
`PUBLIC_BASE_URL`, so browser based MCP clients on other origins are
refused, on purpose.

## PostgreSQL is supported by configuration, not tested

SQLAlchemy and the Alembic migration are written to run on PostgreSQL
(timezone aware timestamps, named constraints, native booleans), and the
table definitions were compiled with the PostgreSQL dialect, but no
PostgreSQL server or driver was available here, so nothing has run
against one. Column defaults are applied by the application, not the
database, so rows must be written through the models.

## Compiled packages can be blocked by Windows Application Control

**Status: worked around for SQLAlchemy; others may need the same.**

The policy that blocked scipy 1.18 (see above) also refused SQLAlchemy
2.1's compiled speed-ups on the owner's machine: the first
`python -m backend.db.seed` failed with `ImportError: DLL load failed while
importing _processors_cy`. They loaded fine in the shell used for
development, so this was only found on the owner's own terminal. SQLAlchemy
ships a pure Python copy of every compiled module, so the fix is installing
it without the `.pyd` files; the exact command is in requirements.txt.
Other packages that ship only compiled code (bcrypt, cryptography,
pydantic-core, numpy) have no such fallback; they imported fine on the
owner's machine in earlier runs, but a stricter policy would stop the app
at startup with the same kind of error, and the fix would be a different
version or build of that package.


## What was not live tested in the LiveKit, voicekit, avatar, hub and code login work (2026-10-05)

Everything below was built and covered by tests (the suite has 465 tests), but
these parts were not run for real. Read this list before relying on a claim.

- **A real microphone and speakers.** The one full browser run (Chrome, one
  machine, LiveKit Cloud) used a synthetic microphone: a pre recorded spoken
  question fed into the page's real WebRTC publish path. It heard the greeting,
  transcribed the question exactly, answered through the real Orchestrator,
  played the reply track and moved the avatar. Nobody spoke into it, so room
  echo, a quiet or noisy microphone and self interruption were not tested.
- **Barge in with real speech.** It was tested with a fake room and by one live
  harness that sent synthesized speech, not by a person interrupting. While the
  assistant speaks the microphone stays open, so this relies on the browser's
  echo cancellation; loud speakers near a microphone may interrupt the
  assistant by itself.
- **One time codes against the real hub.** The hub's tools were only listed.
  `validate_customer_and_send_otp` was never called (it would send a real SMS),
  so the shape of its answer, the session id field and the telephone number
  format (national, like 0112345678) are assumptions, parsed defensively and
  covered by a mock hub only. An answer that is not clearly a success or a
  failure gives a typed error and a 503, never a guess.
- **SMS gateway and email senders.** Tested against local fakes only; no gateway
  or mail server is configured. The gateway body is a template that will need
  adapting to the real provider.
- **The one time code form in a browser.** The page step was written and its
  endpoints are tested, but it was not clicked through in a browser.
- **Switching the male or female avatar in a real session.** The male avatar
  loads and renders in the standalone demo page; whether its mouth moves under
  audio was not confirmed, and a mid session swap on the voice screen was not
  watched.
- **Sinhala and Tamil mouth movement.** There is no viseme model for either.
  HeadAudio plus a jaw energy floor moves the mouth, and a Sinhala sentence did
  move it, but whether it looks right needs a Sinhala or Tamil speaker. Tamil
  was not exercised in the browser.
- **Other browsers and devices.** Only Chrome on a Windows laptop with
  integrated graphics (about 22 frames per second for the new avatar). Firefox,
  Safari and phones were not tried. HeadAudio needs a secure context (localhost
  or https).
- **Azure and local model voices.** The Azure provider was written from the SDK
  documentation without the SDK or a key. The local Piper provider never
  produced audio here (no model files, no espeak-ng). Kokoro is not implemented.

## LiveKit is tried first, with a fallback; a stale .env hides the new parts

The page uses LiveKit only when the server is set to it, has keys and its voice
agent is running (`agent_ready`), and falls back to `/ws/voice` on a refused
token, a failed connection or an agent that does not answer within 20 seconds.
That timeout is a guess based on one measured start (about 5 seconds to the
greeting), not a tuned value, and it means a failing LiveKit costs up to 20
seconds before the fallback starts when the room connects but no agent arrives.
A `backend/.env` copied from an older template keeps `VOICE_TRANSPORT=websocket`
and `AVATAR_RENDERER=legacy`, which override the new defaults: the page then
shows the old bot and the male and female switch changes only the voice.

## LiveKit Inference writes the replies, and it is slower than the local model

With `CLOUD_LLM_PROVIDER=livekit` every reply is first requested from LiveKit
Cloud's hosted gateway. Measured from the development machine with a one
sentence question, first token took 1.33 to 2.01 s for `openai/gpt-4o-mini`
(3 warm calls), 1.43 s for `openai/gpt-4.1-nano` (1), and 1.95 to 2.15 s for
`google/gemini-2.5-flash-lite` (2), against roughly 0.3 to 0.9 s for the local
Ollama model, so the first word comes about 1 to 1.5 s later than with the local
model alone. That is the price of using the hosted model, and
`CLOUD_LLM_PROVIDER=gemini` or an empty value (local only) brings it back.
Only the reply text comes from LiveKit: speech to text is still the local Whisper
and speech out is still Google or edge-tts. The gateway is billed to the LiveKit
Cloud project (credits or plan), so running out of credit shows up as a failed
call, then local answers for 5 minutes; the breaker is per server, not per
conversation. Not measured: a long conversation, other models, or the gateway
from another network. `openai/gpt-4.1-mini` was tried once and failed in my
probe because of a bug in the probe (a final chunk with no text), which the
real code handles; it was not retried.

## The first word over LiveKit is slower without a cloud voice

voicekit's edge-tts provider returns audio only once a whole sentence is
synthesized, so over LiveKit the first word took 2.3 to 3.0 s after the final
transcript with edge-tts alone, against 0.81 and 1.29 s with Google, and
`/ws/voice` with Google took a median 0.94 s. voicekit passes the configured
cloud credentials only when the caller gives them, which the app does
(`config.py` to `VoicekitSettings`). If `GOOGLE_APPLICATION_CREDENTIALS` is
empty in `.env` (it was, during part of this testing), both transports fall to
edge-tts. A streaming edge-tts decoder would remove most of the gap and was not
written.

## The hub's knowledge search is too slow for a spoken reply

Against the real hub (SSE only, 24 tools), `search_knowledgebase_tool` took 3.2
to 7.0 s per call, and it overlaps the local knowledge base. It is therefore off
the reply path by default (`MCP_HUB_IN_REPLIES=false`); the hub is still used
for one time codes. Turning it on adds up to `MCP_HUB_CONTEXT_BUDGET_SECONDS`
(1 s) to a turn, and a timeout there opens the circuit breaker that the hub's
code tools share, so one time codes would fall back to the local backend for
`MCP_HUB_BREAKER_SECONDS`. The local backend only works for customers that have
a mobile number or email stored on their account (`otp.service.set_contact`).

## The new avatar's male model is generated, not committed

`male.glb` (18 MB) is produced by `frontend/avatar/tools/make_male_avatar.py`
in headless Blender from CC0 MakeHuman assets and TalkingHead's rig, and is git
ignored like `mpfb.glb` (downloaded by `fetch_assets.py`). Without it the
toggle changes only the voice and shows a short notice. Two of its inputs
(Visemes 02 and Faceunits 01) are described as CC0 on the MakeHuman pages but
the licence file inside each zip was not read; confirm before shipping
(docs/THIRD_PARTY.md). The model wears a plain blue T-shirt and jeans (a CC0
MakeHuman outfit, texture repainted without its logo), is barefoot, has a static
hair mesh and is not compressed. It was checked in a head and shoulders view;
the trousers and arms in full body framing were not looked at.

## Licence and policy decisions that are not mine to make

- **GPL in the local voice route.** piper-tts and espeak-ng are GPL-3.0, and
  Kokoro needs espeak-ng too. The local model provider is off unless model
  files are present and nothing is installed by default; shipping it needs a
  decision.
- **edge-tts** calls an unofficial Microsoft endpoint. It is the existing
  default for Sinhala and Tamil and the only free neural voice for them; whether
  it is acceptable in a company product is a policy question.
- **livekit-agents** declares `openai<3`, while this project pins openai 3.17.0
  for its own use. The LiveKit packages are installed without dependencies, so
  pip prints a warning and nothing breaks; the pin is documented in
  requirements.txt.

## Smaller things

- Over LiveKit the agent greets every time a room is joined, so a dropped and
  rejoined conversation is greeted again (the `greet` flag of `/ws/voice` is not
  sent). Dispatch and job start took about 5 s before the greeting began in the
  one live test.
- `/ws/voice` has no browser audio, so the new avatar can only follow the
  sentence text there (marked in the code); real audio lip sync needs LiveKit.
- The local code backend sends on a worker thread so response time does not
  reveal whether a number exists; the price is that a delivery failure is logged
  but not reported to the person asking.
- `tests/test_knowledge.py::test_live_roaming_retrieval` failed once while the
  machine was busy with the app, a browser and Whisper, and passed alone.
- The Alembic revision 0002 (contact columns and the code table) was applied to
  the development database `data/app.db` during testing.
- The livekit native library loaded on this machine; a stricter Application
  Control policy could refuse it like the packages in the section above, and the
  app would then keep `/ws/voice` (the import is guarded).

## Fallback chain, guest access and verification wizard (2026-10-09)

What was checked live: the app was run on a throwaway database. A guest asking
for a bill was blocked in English, Sinhala (script and Latin letters) and
Tamil. The wizard was walked over HTTP in English (wrong name and unknown number
returned the identical answer, cooldown, a correct code after 125 seconds of
real time was rejected as expired, a new code verified once and could not be
reused, the cookie unlocked the same question). In Chrome the wizard was
completed in Sinhala including a wrong code with its localized message and the
greeting by name, and step 1 was rendered in English and Tamil at 360 px with no
horizontal scroll. With a bad Gemini key and a bad LiveKit secret the chain fell
to local Llama and `/health` showed each tier's reason. The full suite passes
(809 tests) with fakes for every provider.

Not live tested:
- **Gemini**: no working key here. The tier is tested with fakes; a real safety
  block from the Gemini SDK and a real 402 or 429 were never seen. A bad key
  gave HTTP 400, which the chain treats as unavailable with reason "other".
- **LiveKit Inference quota**: the free plan's models and the exact quota
  error were not found in the documentation read; 402 and 429 are mapped by
  status code only.
- **Real delivery**: SMS gateway and SMTP senders, and their Sinhala and Tamil
  message bodies on a phone, were never sent. The console sender was used.
- **The MCP hub**: it has no customer lookup or send tool, so none was called.
  The tool gate is tested against the local mock hub. The hub may want the
  number as 0112150026 (as its tool help shows) and the gate sends that form; a
  real call was not made.
- **The customer directory holds fake rows.** A real source of names, mobile
  numbers and emails must be supplied before this is useful. Until then every
  real customer is refused.
- **Verified users get the same model as guests.** The model has no customer
  data tools wired yet, so a verified user asking for a bill is not shown one;
  in the live run the model said it could not see the account (and invented a
  hotline number). Verification unlocks the path and the server gate, not
  customer answers yet.
- **The system prompt access clause reaches the cloud tiers only.** The local
  Llama agent builds its own prompt; the guard is what protects it.
- **PIN logins count as guests** for customer data, by design, since only the
  one time code sets `amr: ["otp"]`.
- **Keyword rules are blunt.** Words such as "activate", "paid", "ticket" or
  "not working" block guests even in general questions. The classifier's
  threshold (0.53) and weights are untuned. Sinhala and Tamil rules use plain
  substring matching and were written without native review.
- **Sinhala and Tamil wording** (wizard, blocked message, OTP texts) is machine
  written and needs a native speaker. The terms and privacy links go to the
  English pages, because /si/ and /ta/ versions return 404.
- **The browser run covers Sinhala fully.** English and Tamil were checked only
  to step 1; the step list for a full run is tests/browser/wizard_e2e.md.
- **Voice**: the guard and the chain were tested over `/ws/voice` with fakes and
  over the LiveKit session with fakes. A spoken question through a real
  microphone was not tried.
- **Several server workers**: the verification lookup brake is locked per
  process; the database counted limits still hold.
- `test_live_roaming_retrieval` needs a working local model and times out on
  this machine at times.

## Interface layer, phone layout and hub check (2026-10-13)

**Lighthouse (mobile, language screen, default throttling).** Single runs on
this machine are very noisy: the unchanged frontend scored 41, 56, 58, 67, 72,
72, 80 and 81 on different runs, depending mostly on whether first paint
happened before or after a one second task. So the comparison below uses
interleaved runs of the committed frontend and the new one, served the same way.

| Round | Committed frontend | New frontend |
|---|---|---|
| first, 4 runs, landing started at DOMContentLoaded | 67, 72, 72, 58 | 66, 56, 55, 54 |
| after starting the landing at the top of the body, 3 runs | 56, 81, 80 | 66, 76, 76 |

Accessibility went from 98 to 100 (the page now has one main landmark; the
single baseline failure was landmark-one-main). Best practices stayed at 96.
Performance in the last round is about 4 points lower on the median (76 against
80), inside the run to run spread, with the largest contentful paint about 0.4 s
later (4.2 s against 3.8 s). That is the cost of the extra files and the landing
overlay, and it is not better than before. What was done about it: the three
stylesheets that are only needed after the first screen load without blocking,
the scripts are deferred, and the landing starts at the top of the body.
Not done: the 3D avatar script (bot3d.js, not part of this work) builds its
scene at page load and takes about one second of main thread time; when first
paint comes earlier than that task, Lighthouse counts it as blocking time (about
400 to 950 ms in those runs), and it did the same on the committed frontend. Starting
that scene only when the voice screen opens would remove it; it was left alone
because it changes existing behavior. The very first baseline run (31) was probably a
cold CDN fetch (largest contentful paint 201 s) and is not comparable.

**Only emulated.** Phone layouts, touch targets, safe area insets and dynamic
viewport units were checked in desktop Chrome with emulated phones (360x740,
390x844, 360x640, 480, 768, 1024, 1440 and a landscape 740x360). Not run on a
real iPhone, Pixel or Galaxy, and not in Safari or Firefox (the Playwright
builds for them are not installed here, so their tests skip). The `:has()`
rule that hides the bottom bar under the history sheet needs Chrome 105, Safari
15.4 or Firefox 121; on older browsers the bar stays visible above the sheet.

**Not checked.** Screen readers; the intro sound; the low power branch (no
blur) because this machine reports enough cores and memory; the Sinhala and
Tamil rendering with the real Noto fonts (the tests block Google Fonts, so
system fonts were used); the admin dialogs with a signed in admin (the layer is
loaded there, only copied markup was tested); the landing on a real slow
phone; the mode chip over LiveKit (it follows the agent_response mode field,
which the WebSocket path sends).

**Known behavior.** The landing covers the first screen for up to 2.5 s on the
first visit in a session and swallows the first click or key that skips it. The
wizard's wrong code and rate limited messages share one icon because the page
text cannot tell them apart. The Sinhala and Tamil text for all new strings is
machine written and needs a native speaker. The voice screen's mode chip only
shows on the phone layout.

**Hub.** The hub speaks the older SSE transport at /sse; registering it over
streamable http at the root fails. `CUSTOMER_DATA_SOURCE` stays `local`
because the hub has no customer lookup tool (see docs/MCP_HUB.md). The check
command was run against the real hub for the tool list only; its optional read
only calls and any customer tool were not run there.

**Tests.** `tests/ui` is opt in (about 13 minutes here) and needs the dev
packages playwright, pytest-playwright and axe-playwright-python plus the
system Chrome. A few of its checks are timing based (the landing duration must
be under 2.5 s) and can fail on a heavily loaded machine.
