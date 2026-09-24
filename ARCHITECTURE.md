# Architecture

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
during a demo, not just an assertion.

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

## Cloud multi agent collaboration versus local fallback

```
                    Orchestrator.respond(text)
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

## Design tradeoff carried over from the prototype

The `ChatAgent` interface is still `respond(text_input: str) -> str`, one
method, synchronous, stateless. This keeps every agent (Echo, local Llama,
Gemini, GPT, and the Orchestrator itself, which is also a `ChatAgent`)
trivially swappable and testable in isolation, at the cost of no built in
conversation memory across turns. See README.md's suggested next steps.
