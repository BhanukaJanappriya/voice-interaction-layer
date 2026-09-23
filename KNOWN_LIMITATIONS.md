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

## The agent's reply streams in real time on the voice path only, and only for the local fallback

**Status: genuinely real time where implemented; a deliberate scope boundary elsewhere.**

The WebSocket voice pipeline now streams the agent's reply as it is
generated: `LlamaOllamaAgent.respond_stream()` (`backend/agent.py`) calls
Ollama with `stream: true` and yields each token chunk as it arrives,
`server.py` forwards the accumulated text to the frontend as
`agent_response_chunk` events, and the frontend shows the reply appearing
live in the transcript during the "thinking" status, not only once the
full reply is ready. This was verified against the real local Ollama
server, not mocked: a test request produced two separate chunks
("Hello", "!") arriving as separate WebSocket messages, and a longer
prompt produced many chunks building up the sentence progressively.

Two scope boundaries on this, stated plainly rather than implied away:

- Only the local Llama fallback path is actually streamed chunk by
  chunk. The cloud multi agent path (reasoning, then tool/retrieval, then
  composing, see ARCHITECTURE.md) still runs its three calls to
  completion and yields the finished reply as a single chunk.
  `Orchestrator.respond_stream()`'s own docstring explains why: streaming
  a three stage pipeline, and deciding what a viewer should see of the
  internal reasoning stage while it is in flight, was judged out of scope
  for this pass. Since no cloud credentials are configured in this
  environment, this path has not been exercised live either way (see the
  cloud multi agent entry below).
- The text chat screen's `POST /chat` endpoint is not streamed; it is a
  plain request/response call that waits for the full reply, unlike the
  WebSocket voice path. Streaming a plain HTTP POST would need Server
  Sent Events or a similar mechanism on both ends, which was not built in
  this pass since the voice path already demonstrates real streaming
  end to end.

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
buffer every few chunks (about every 2 seconds of new speech) and finishing
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

## The Gemini SDK in use is deprecated, though still functional

**Status: works today, flagged for a future migration.**

`backend/agent.py`'s `GeminiAgent` uses the `google.generativeai` package,
which Google has deprecated in favor of `google.genai`. It still works
(confirmed live against a real account, once the model name was updated
to `gemini-flash-latest`, see above) but prints a `FutureWarning` on
import. Migrating to `google.genai` is a real fix worth doing, just out
of scope for this pass since the deprecated package has no announced
removal date and nothing in this project depends on the newer package's
specific features.

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
turns.

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
The name only changes what the greeting says and reaches no other part of
the system; the backend agent has no notion of who is asking, and per turn
personalization in its own replies (not just the fixed greeting) was
judged out of scope for this pass.

**Real fix:** an actual account system (even a simple one, a persisted
user record keyed by something more durable than a browser's local
storage) if genuine returning user recognition across devices is needed;
and, separately, passing the name to the backend so the agent's own
replies, not just the canned greeting, can address the user by name.
