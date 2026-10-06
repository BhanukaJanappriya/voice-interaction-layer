# Architecture: Pluggable Chat Agent Design

This document explains two things in detail: the real time data flow
through the backend, and the cloud multi agent collaboration design versus
its local single agent fallback, since that distinction is the core new
requirement for this pass.

## Real time data flow

```
 Browser                          FastAPI backend (server.py)
 =======                          ===========================
 mic -> resample to 16kHz  =>     binary WebSocket frames
                                       |
                                       v
                              VoiceActivityDetector (vad.py)
                              calibrated once per session,
                              gates every chunk: speech or not
                                       |
                                       v
                              transcribe_stream() (stt.py)
                              runs on a background thread so
                              blocking whisper/cloud calls do
                              not stall the event loop
                                       |
                     partial events =>|<= final event (silence detected)
                                       |
 status/transcript  <=  JSON  <=======+
 events over the                      |
 same WebSocket                       v
                              Orchestrator.respond_stream() (agent.py)
                              cloud multi agent or local Llama alone,
                              both streamed as they are generated
                                       |
 agent_response_chunk events  <= JSON <+  (repeated while generating)
 agent_response event  <=  JSON  <====+   (once, when done)
                                       |  each finished sentence is
                                       v  handed on straight away
                              speak_stream() (tts.py)
                              synthesis thread prepares the next
                              sentence while the playback thread
                              plays the current one; cloud neural
                              voice (streamed), edge-tts, or local
                              SAPI5 for English
                                       |
 speaking_sentence events <= JSON <===+   (as each sentence starts)
                              plays on the server machine's
                              own audio output (see the
                              "server side playback" note in
                              README.md's suggested next steps)
```

Every stage's own latency is printed by that module (stt.py, agent.py,
tts.py each already did this in the prototype); server.py adds one more
line per turn with the time from the final transcript to the first spoken
audio, so real time performance claims have actual numbers behind them
during a demo, not just an assertion. The knowledge retriever prints its
own line too ("Retrieval latency: N ms").

## One deployable, still modular

```
                 python -m backend  (one uvicorn process, one config.py,
                        |            one database, one startup sequence)
                        v
  startup:  power throttling opt out -> Alembic migrations -> first admin
            seed -> MCP session manager -> background warm up (Whisper,
            Ollama, TTS, knowledge index)

  routes in backend/server.py
  +-----------------+-------------------------------------------------------+
  | /               | frontend/ (static)          the user facing app       |
  | /admin/         | frontend/admin/ (static)    admin portal page         |
  | /ws/voice       | server.py                   voice pipeline            |
  | /chat           | server.py                   text chat                 |
  | /health         | server.py                   status of every part      |
  | /auth/*         | backend/auth/routes.py      user and admin sign in    |
  | /admin/api/*    | backend/admin/routes.py     agents, MCP clients, audit|
  | /oauth/token,   | backend/mcp_server/oauth.py MCP token endpoint and    |
  | /.well-known/*  |                             discovery metadata        |
  | /mcp            | backend/mcp_server/mount.py MCP tools (streamable HTTP)|
  +-----------------+-------------------------------------------------------+

  modules behind them (each with its own interface in INTERFACES.md)
    agent.py  stt.py  tts.py  vad.py            unchanged contracts
    knowledge/   retriever, index, scraper      Orchestrator tool_callback
    db/          models, Alembic migrations     SQLite or PostgreSQL
    auth/        phone, PIN, tokens, admins
    admin/       external agents service        Orchestrator external_agent_provider
    mcp_server/  tools, OAuth, audit
```

## The retrieval (RAG) path

```
 offline, python -m backend.knowledge.build
   www.sltmobitel.lk + www.mobitel.lk  --scraper.py-->  data/knowledge/raw/ (cache, robots.txt obeyed)
   public_sources.jsonl, faq.jsonl     --normalize.py-> docs with source_url, retrieved_at, package fields
                                       --index.py-----> chunks (about 160 tokens, 30 overlap; packages and
                                                        FAQ entries whole) -> bge-m3 (Ollama) ->
                                                        data/knowledge/index/vectors.npy + chunks.jsonl

 per turn, inside Orchestrator.respond_stream()
   final transcript
        |
        v
   retriever.context_for(text)   embed the question (bge-m3, ~30 ms warm),
        |                        one matrix product over all chunks,
        |                        keep scores >= RETRIEVAL_MIN_SCORE (0.53),
        |                        keyword boost for exact words, best 3 chunks,
        |                        each cut to the sentences matching the
        |                        question, tagged with source and date
        |                        (None for small talk, a missing index or
        |                        Ollama down: never raises)
        v
   cloud path: the tool/retrieval stage's output, handed to the composer
               with the rules in its role prompt
   local path: rules + context in the Llama system prompt
   the rules: SLT-Mobitel facts only from the context (never invented),
   anything else answered from the model's own knowledge, like a person
               (context for one model, not multi agent collaboration)
        |
        v
   streamed reply -> SentenceChunker -> speak_stream(), as before
```

## Verification and session flow (existing users)

```
 browser                               backend
 Existing user: landline + PIN
   POST /auth/verify  ---------------> normalize to E.164 (valid SL landline area code)
                                       per address rate limit (10 per minute)
                                       account locked? -> 423 + Retry-After
                                       bcrypt check of the PIN (also run for an unknown
                                       number, so timing reveals nothing)
                                       5th failure in a row -> locked for 15 minutes
   <--------------------------------- 200 {display_name, token} + httpOnly cookie user_session
                                       (JWT, audience "user", 60 minutes)
   WS /ws/voice start_session
     {session_token} or the cookie --> verified_user_from_request(): signature, expiry,
                                       audience, user still exists
                                       -> user_name = the account's display name
                                       (a client supplied user_name only counts for a Guest)
   POST /chat (Bearer or cookie) ----> same resolution
```

## MCP authorization flow (machine clients)

```
 MCP client                            backend (its own authorization server)
   POST /mcp (no token) -------------> 401 WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/mcp"
   GET that metadata ----------------> {resource: .../mcp, authorization_servers: [base]}
   GET /.well-known/oauth-authorization-server -> token_endpoint, grant client_credentials
   POST /oauth/token (client id + secret, scope) -> bcrypt check of the secret, client not revoked
   <--------------------------------- JWT, audience .../mcp, scopes, 15 minutes
   POST /mcp tools/call, Bearer ------> verify signature, expiry, audience, client still active
                                       tool needs its scope, else 403 insufficient_scope
                                       one audit row (client, tool, outcome, duration; no content)
```

## Where the delay went, and what fixed it

Measured with an automated end to end WebSocket test on the development
machine (Google Chirp3-HD voice, local Llama, local Whisper), feeding a
real spoken question and timing every event:

| Stage | Before | After | Cause and fix |
| --- | --- | --- | --- |
| Each Whisper pass | ~2.4s | ~0.45s | Windows power throttling (EcoQoS) ran the server, a background process, on efficiency cores at low clock. The server now opts out at startup (`_opt_out_of_windows_power_throttling` in server.py). |
| Each Ollama request | +2.0s | +0.03s | `localhost` is tried as IPv6 first on Windows; Ollama only listens on IPv4. The host is now `127.0.0.1`. |
| Waiting for the reply to start speaking | whole reply generated first | first sentence (or first clause) | The reply is spoken sentence by sentence while it is still being generated. |
| Google TTS, per sentence | ~1.4s before any audio | ~0.3s | Switched to Google's streaming synthesis API and start playing on the first audio chunk; the next sentence is synthesized while the current one plays. |
| Failing cloud LLM call | ~1.1s every turn | 0 | Circuit breaker: after a cloud failure, skip it for 5 minutes; also probed at startup. |
| First question of a session | +15s of model loading | 0 | Whisper, noisereduce/scipy, Ollama and the TTS connection are warmed up when the server starts. |
| End of turn detection | 1.5s of silence | 1.0s | `SILENCE_CHUNKS_TO_FINALIZE` lowered (see KNOWN_LIMITATIONS.md for the tradeoff). |

Net effect in that test: the assistant starts speaking about 0.8 seconds
after the final transcript (previously 4 to 9 seconds), and the greeting
starts about 0.2 seconds after connecting.

### Before and after the knowledge base and the speech changes (2026-10-02)

Same harness (edge-tts spoken questions streamed in 0.5 s chunks over
/ws/voice, local Llama because the Gemini key is unusable, Google TTS),
same machine, first audio measured from the final transcript:

| Measure | Before (2026-09-30) | After | Why |
| --- | --- | --- | --- |
| First audio after the final transcript | 0.83, 0.62, 0.81 s (median 0.81 s) | 1.19, 1.12, 1.08, 1.05, 1.39, 0.96, 1.10, 1.23 s (median 1.11 s) | retrieval plus a longer, grounded first sentence |
| Retrieval, inside the running server | none | 148 to 176 ms (8 turns) | bge-m3 query embedding plus search; 75 ms p50 / 105 ms p95 on its own in the evaluation. Slightly over the 150 ms target while Whisper and Llama are also loaded |
| Local Llama time to first token, uncached prompt | 180 ms (no context) | 321 ms with context (382 ms with the first knowledge base design) | three chunks of at most 700 characters cut to the matching sentences, shorter rules |
| Local Llama answer time p50 | 911 ms (first knowledge base design) | 632 ms | system prompt 2,757 characters instead of 3,911 |
| English answers containing the expected facts | 6% | 79% | docs/RAG_EVAL.md |
| Word errors on 144 spoken test clips | 11.0% (noise filter on, no hotwords) | 3.3% | see the speech table below |

Grounding now costs about 0.3 s of extra wait before the first word
(0.7 s with the first design), most of it the local model reading the
notes. Turns with no relevant notes (small talk, general questions) pay
only the retrieval. Retrieval was cut down by sending only the best three
chunks, each reduced to the sentences that match the question, with short
prompts chosen by code (strict with notes, general without) and Ollama
bounded (temperature 0.3, at most 220 generated tokens, a 2048 token
context). The cloud path, when available, reads the context much faster
than the local 3B model.

### Speech to text: what was measured and what changed

144 spoken clips (36 sentences, three voices: US English and two Indian
English voices, the nearest available to Sri Lankan English; one voice also
with background noise at about 12 dB), local faster-whisper, int8, CPU,
power throttling off. Word error rate counts spelling variants such as
fiber/fibre as errors, so absolute values read high.

| Setup | Word errors | Brand words found | Per clip |
| --- | --- | --- | --- |
| base, noise filter on (before) | 11.0% | 46% | 462 ms |
| base, noise filter on, hotwords | 7.3% | 73% | 497 ms |
| base, no noise filter, hotwords (the default) | 3.3% | 85% | 469 ms |
| small, no noise filter, hotwords | 2.4% | 91% | 1477 ms |
| small.en (English only) | 2.0% | 89% | 1497 ms |
| base.en (English only) | 3.8% | 81% | 505 ms |

- The noise reduction filter was hurting: Whisper copes with noise on its
  own, and the filter's artefacts made it worse. It is off by default
  (`STT_DENOISE`).
- With the brand hotword set, Whisper wrote "SLT Mobitel" for audio with no
  speech at all (30 of 30 noise clips with `small`, 7 of 30 with `base`).
  Whisper's own speech filter (`vad_filter`, `STT_VAD_FILTER`) brings that
  to 0 of 30 with identical accuracy and speed, so it is on.
- `small` is about 3 times slower per pass. Running it on every turn would
  add a second to each reply, so only a final transcript the main model is
  unsure of (average log probability below -0.40, about 19% of the test
  clips) is rechecked with it, which takes word errors from 3.3% to about
  2.8%; partial transcripts shown while the user speaks never use it.
  Confidence is only a weak predictor of errors (mean -0.40 for wrong
  transcripts, -0.32 for right ones), so this recovers about half the gap.
- Tamil: character errors 21.1% with `base`, 6.7% with `small` (six spoken
  questions), so the recheck matters most there.
- Sinhala: neither local model can transcribe it (base writes romanized
  nonsense, small writes garbage), so Sinhala voice input needs Google
  Cloud Speech-to-Text, which is built in but not enabled in the Google
  project yet (see KNOWN_LIMITATIONS.md).

## Cloud multi agent collaboration versus local fallback

```
                    Orchestrator.respond(text)
                              |
              external agent active (admin portal)?
                 yes: ExternalHttpAgent answers; its text is spoken by
                      this project's own TTS pipeline. ExternalAgentError
                      before any text -> continue below for this turn.
                              |
                cloud LLM provider configured?
                    (an API key is present)
                    /                      \
                  yes                       no
                   |                         |
        try the cloud multi              LlamaOllamaAgent
        agent sequence  ==fails=>         handles the full
                   |                      reply ALONE
                succeeds                       |
                   |                           |
        (optional reasoning agent) ->          |
        tool/retrieval agent -> composer       |
        agent (streamed), all served by        |
        the same configured cloud              |
        provider with different role           |
        prompts                                |
                   |                           |
                   +=============+=============+
                                 |
                    final reply text, plus
                    Orchestrator.last_mode
                    logged and sent to the
                    frontend so the active
                    mode is always visible
```

**Why three roles against one provider, rather than three separate
models.** The requirement is genuine multi agent collaboration (a
reasoning step, a tool/retrieval step, a composing step) when a cloud
model is reachable. Standing up three genuinely different model backends
per role was judged out of scope for this pass's timeline (see the Scope
Boundary in the original brief); instead, the same configured provider
(Gemini or GPT) is called three times with three different system prompts
and a chained context (the reasoning agent's output feeds the composer,
and the tool/retrieval hook's output, if any, is folded in too). This is a
real architectural split, not a single call dressed up: each stage has a
distinct responsibility and its own prompt, and the composer never sees
the raw internal reasoning text exposed to the user. A future pass could
swap in genuinely different backends per role (for example, a fast local
model for tool routing and a stronger cloud model for composing) behind
the exact same Orchestrator shape, since nothing about the interface
assumes same-provider roles.

**Why the reasoning stage is now optional (off by default).** The
reasoning call has to finish completely before the composer can even
start, so it roughly doubled the time before the assistant said its first
word, for little gain on short spoken questions. It is still there behind
`CLOUD_REASONING_STAGE=true` in `.env`, so the three role design can be
shown, but the default favors a fast spoken reply.

**Why the fallback is all-or-nothing.** If the cloud path fails partway
(say, the reasoning call succeeds but the composer call then hits a quota
error), the Orchestrator does not try to patch together a partial cloud
result with a local completion. It catches `CloudUnavailableError` from
the failing call and restarts the whole turn on `LlamaOllamaAgent` alone.
Now that the reply is streamed, the one exception is a failure after the
cloud has already produced text that is being spoken: switching model mid
sentence would sound worse, so a short apology is added instead.
A clean either/or is much easier to reason about, test, and explain in a
demo than a hybrid path, and it matches the requirement's own wording:
"no attempt at multi agent collaboration in that fallback mode."

**What has actually been exercised.** With no cloud credentials configured
on this development machine, the local fallback path has been run
repeatedly, including through an automated end to end WebSocket test (see
KNOWN_LIMITATIONS.md), and it correctly falls back and logs
`local single agent (Llama via Ollama)` as the mode every time. The cloud
multi agent path is implemented and has been reviewed for correctness
(prompt construction, error handling, the `CloudUnavailableError` catch),
but has not been run against a live Gemini or GPT account, since no key is
available here. That is a real gap between "built" and "proven," stated
plainly rather than implied away.

**Retrieval on each path.** The tool/retrieval stage is now real: the
SLT Mobitel knowledge base retriever is plugged into the same
`tool_callback` hook the prototype stubbed out. On the cloud path its
output is handed to the composer as the retrieval stage's result. On the
local fallback the same context goes into the Llama system prompt; that is
one model given extra context, not multi agent collaboration, and it is
described that way everywhere.

**External agents sit in front, with the same fallback rule.** An external
chat agent made active in the admin portal answers first. Its text goes
through exactly the same SentenceChunker, speak_stream() and events as a
built in reply, so the avatar and captions behave the same. If it fails
before producing text, that turn falls back to the cloud/local path; if
it fails mid reply, a short apology is added, as on the cloud path.

## Design tradeoff carried over from the prototype

The `ChatAgent` interface is still `respond(text_input: str) -> str`, one
method, synchronous, stateless. This keeps every agent (Echo, local Llama,
Gemini, GPT, and the Orchestrator itself, which is also a `ChatAgent`)
trivially swappable and testable in isolation, at the cost of no built in
conversation memory across turns. See README.md's suggested next steps.


## Real time browser audio, reusable voice, new avatar, hub login (2026-10-05)

Four additions sit next to the original pipeline. Nothing was removed:
`/ws/voice`, server side playback and `bot3d.js` still work and are chosen by
`VOICE_TRANSPORT` and `AVATAR_RENDERER`.

### Two transports, one pipeline

```
 WebSocket transport (original, VOICE_TRANSPORT=websocket)
 browser mic --float32 16 kHz chunks--> /ws/voice --> VAD --> STT --> Orchestrator --> tts.py --> server speakers
                                          ^ status, transcripts, replies as JSON events back to the page

 LiveKit transport (VOICE_TRANSPORT=livekit)
 browser mic --WebRTC (echo cancel, noise suppress)--> LiveKit room
        --> LiveKitVoiceSession (backend/livekit_transport, one per conversation, run by a livekit-agents job)
              resample 16 kHz --> the same VAD --> the same STT --> the same Orchestrator
              reply text --> voicekit.synthesize_stream --> 24 kHz audio track "agent-voice" --> back to the browser <audio>
              events (/ws/voice JSON shapes + interrupt) on data topic va-events --> the page's existing handler
        <-- page controls on data topic va-control (set_voice, mute)
 POST /api/livekit/token (short lived room token for a verified user or a Guest) starts it
```

The page asks `GET /api/livekit/config` once. If the server is set to LiveKit
and configured, the page joins a room; on any failure (no keys, token refused,
connection fails) it opens `/ws/voice` exactly as before. The worker runs its
jobs as threads of the app process, so Whisper and the knowledge index are
loaded once, not twice.

Barge in: while the assistant speaks, the session keeps running the voice
activity detector on the user's microphone. After about 200 ms of speech it
cancels synthesis, clears the audio source queue, sends `{"type":"interrupt"}`
and starts listening, seeding the next utterance with the last 400 ms so the
first word is not lost. The page stops the mouth on `interrupt`.

### voicekit

`backend/voicekit` is the text to speech chain (cloud, edge-tts, local model,
SAPI5) as a package that imports nothing from the rest of the project. It
yields 16 bit PCM chunks and word timings, can play to the speaker, an HTTP
chunked response, a WebSocket or a LiveKit audio source, and has an API
(`POST /api/tts/stream`, `WS /ws/tts`, API keys, rate limits) so another agent
can speak. See docs/VOICEKIT.md. `tts.py` is untouched and still speaks for
`/ws/voice`; the voice map is kept equal by a test.

### Avatar

```
 AVATAR_RENDERER=talkinghead  --> frontend/avatar/loader.js --> avatar.js (TalkingHead, MIT, three.js 0.180 via import map)
      real audio (LiveKit track or voicekit)  --> HeadAudio visemes (language independent) + jaw energy floor
      voicekit word timings (English)         --> visemes placed on the audio clock
      WebSocket transport (audio is on the server) --> text driven mouth, NOT audio lip sync
      any failure (no WebGL, model missing, 20 s timeout) --> event avatar:fallback --> bot3d.js, then the flat face
```

A male/female switch in the voice screen's top bar changes the avatar and the
voice together (`female_natural` or `male`), is remembered in the browser,
and is sent as `voice_style` on `start_session` (or `set_voice` mid session).

### MCP hub and login

The hub (https://mcphub.sltdigitallab.lk) speaks MCP over SSE, behind an API key
in a header; streamable HTTP did not work against it. `backend/mcp_client` detects the
transport, caches it, and has a timeout, retries and a circuit breaker. It
exposes 24 customer, package and knowledge tools; only `search_knowledgebase_tool`
may reach the Orchestrator, and tools that buy, add, subscribe, log faults or
refresh tokens are refused in code even if listed. One time code login
(`backend/otp`) uses the hub's own `validate_customer_and_send_otp` and
`verify_otp_and_authenticate` when the hub is reachable, and a local hashed
design (6 digits, 5 minutes, 3 attempts, cooldown, per number and per address
limits, masked destination) otherwise. The PIN login is unchanged.

### Latency, before and after (2026-10-06, same machine)

Measured with `scripts/measure_first_audio.py` (a spoken question streamed to
`/ws/voice`, clock from the final transcript to the "speaking" status, local
Llama because the Gemini credit is used up). Warm runs; the first run after a
start pays for loading models and is listed apart.

| Path | Provider | Runs | First audio after the final transcript |
| --- | --- | --- | --- |
| `/ws/voice` before any change | Google streaming TTS | 5 warm (1 cold: 18.9 s) | median 0.87 s (0.73 to 0.90) |
| `/ws/voice` after | Google streaming TTS | 8 | median 0.94 s (0.81 to 1.63) |
| `/ws/voice` after | edge-tts (Google credentials blank in `.env`) | 8 | median 1.06 s (0.69 to 3.53) |
| LiveKit, real browser, synthetic microphone | edge-tts through voicekit | 4 | 2.3 to 3.0 s |
| LiveKit | Google through voicekit | 2 (server log) | 0.81 and 1.29 s |

What this shows: the original path is unchanged within noise (0.87 against
0.94 s). Over LiveKit the first word costs about the same as `/ws/voice` when
the same cloud voice is used, but about 1.5 s more when only edge-tts is
available, because voicekit's edge provider waits for a whole sentence before
its first audio (about 2 to 3 s here, 0.6 s when measured alone). The number
of LiveKit runs with Google is two; it is not a distribution. A reply from the
hub's knowledge search takes 3 to 7 s, so it is kept out of spoken replies by
default (`MCP_HUB_IN_REPLIES=false`).

### Audio to mouth offset

Measured in Chrome (Intel integrated graphics, about 22 fps, so one frame is
about 45 ms) on the live LiveKit audio track: median 69 ms between audio onset
and the first mouth movement (30 ms after subtracting the browser's output
latency of 41 ms), 7 samples. Agent D measured 62 ms (22 ms) on a sample file
with 4 samples. These are single numbers good to about one frame.
