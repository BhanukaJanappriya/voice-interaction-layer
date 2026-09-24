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

**Status: partially solved locally; cloud is materially better.**

faster-whisper's multilingual model does support Sinhala and Tamil without
any extra setup, and `backend/stt.py` will use it as a fallback for those
languages. But Whisper's published accuracy on Sinhala and Tamil is well
below its English accuracy (both are lower resource languages in its
training data), and this has not been benchmarked on this project with
real Sinhala/Tamil speech samples, since none were available in this
environment. Treat the local fallback for these two languages as usable
for a rough demo, not as a reliable transcript.

**Real fix:** Google Cloud Speech to Text, which has explicit `si-LK` and
`ta-IN` language codes and materially better accuracy for both. The
adapter (`_transcribe_cloud_google` in `backend/stt.py`) exists and is
wired into the fallback chain but has not been exercised against a live
account for the same reason as above.

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

Diagnosed live on 2026-09-24: the key itself is valid (listing models
succeeds), `gemini-2.5-flash-lite` and `gemini-2.5-flash` are retired for
new users (404), so the default is now `gemini-3.5-flash-lite`, and every
available model returns 402 because the project's prepaid credit is used
up. That last one can only be fixed in AI Studio billing.

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

**Status: solved, but the cause is worth knowing.**

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

## "Existing user" is a name to be addressed by, not a real login

**Status: honest local personalization, not authentication.**

The new or existing user screen (`frontend/index.html`'s `#screen-user`,
logic in `frontend/app.js`) asks whether a visitor is new or existing.
Choosing "Existing user" asks for a name and remembers it in that
browser's `localStorage` so it can prefill the field on a later visit in
the same browser; choosing "New user" addresses them as Guest. There is no
account system, no password, and nothing is checked against anything: two
different people typing the same name are treated identically, and the
same person on a different browser or device starts over as a stranger.
The name is sent to the backend with the session, used in the spoken
introduction, and given to the agent on every turn so its own replies
address the user by name (or as Guest when none was given). The
introduction itself is in the language picked on the first screen
(English, Sinhala or Tamil, `_GREETINGS` in `backend/server.py`), with
"Guest" also said in that language. The agent's replies and the canned
closing lines follow the same selected language (`_REPLY_LANGUAGES` in
`backend/agent.py`), and so does the voice that speaks them.

**Local Llama cannot really answer in Sinhala or Tamil.** Tested on
2026-09-24 with llama3.2 (3B): asked in Sinhala, it produced Sinhala
script that is not meaningful Sinhala; asked in Tamil, it produced
grammatical Tamil with a factually unrelated answer. The language
instruction is followed, the model simply is not good enough in those
languages. Gemini handles both properly, so Sinhala and Tamil replies
need a working Gemini key (see the credit issue above), or a local model
with real Sinhala and Tamil support set as `LOCAL_LLM_MODEL`.
Sinhala is spoken by edge-tts (Google has no Sinhala voice), which took
2.5 to 9 seconds per sentence over the network here, so the Guest version
of the Sinhala introduction is synthesized at server startup and cached;
a named Sinhala greeting still synthesizes its first sentence live. A user can
also say "my name is ..." or "call me ..." during a voice conversation;
the backend picks that up with a simple phrase match
(`_extract_user_name` in `backend/server.py`), uses it from then on, and
the browser remembers it like a typed name. That match is English only,
deliberately ignores "I'm ..." (too often "I'm good"), and uses whatever
Whisper heard, so an unusual name can come out misspelled or as a
similar sounding common name.

**Real fix:** an actual account system (even a simple one, a persisted
user record keyed by something more durable than a browser's local
storage) if genuine returning user recognition across devices is needed.
