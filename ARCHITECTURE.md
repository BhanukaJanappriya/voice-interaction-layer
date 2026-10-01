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
                                       --index.py-----> chunks (about 450 tokens, 60 overlap; packages and
                                                        FAQ entries whole) -> bge-m3 (Ollama) ->
                                                        data/knowledge/index/vectors.npy + chunks.jsonl

 per turn, inside Orchestrator.respond_stream()
   final transcript
        |
        v
   retriever.context_for(text)   embed the question (bge-m3, ~30 ms warm),
        |                        one matrix product over all chunks,
        |                        keep scores >= RETRIEVAL_MIN_SCORE (0.53),
        |                        top 4, each tagged with source and date
        |                        (None for small talk, a missing index or
        |                        Ollama down: never raises)
        v
   cloud path: the tool/retrieval stage's output, handed to the composer
               with the grounding rules in its role prompt
   local path: grounding rules + context in the Llama system prompt
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

### Before and after the knowledge base (2026-10-01)

Same harness (edge-tts spoken questions streamed in 0.5 s chunks over
/ws/voice, local Llama because the Gemini key is unusable, Google TTS),
same machine, first audio measured from the final transcript:

| Measure | Before (2026-09-30) | After | Why |
| --- | --- | --- | --- |
| First audio after the final transcript | 0.83, 0.62, 0.81 s (median 0.81 s) | 1.73, 0.89, 1.53, 1.56, 1.69, 1.25 s (median 1.55 s) | retrieval plus a longer, grounded first sentence, broken down below |
| Retrieval, inside the running server | none | 126 to 159 ms (6 turns) | bge-m3 query embedding plus search; 36 ms p50 / 51 ms p95 measured on its own |
| Local Llama time to first token, uncached prompt | 180 ms (no context) | 404 ms (4 chunks of context) | the model reads about 2,500 more characters before it starts; 2 chunks would be 304 ms |
| Answer contains the expected facts (32 test questions) | 5% | 75% | docs/RAG_EVAL.md |

So grounding costs about 0.7 s of extra wait before the first word, most
of it the local model reading the context and saying a full sentence
rather than an invented short one; retrieval itself sits at the 150 ms
budget inside the server (one turn of six was 159 ms). Turns with no
relevant context (small talk) pay only the retrieval. Cutting the context
to 2 chunks would save about 100 ms at the cost of retrieval recall, so it
was left at 4. The cloud path, when available, reads the context much
faster than the local 3B model.

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
