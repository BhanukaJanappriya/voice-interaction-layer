# Voice Interaction Backend

![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![WebSocket](https://img.shields.io/badge/protocol-WebSocket-informational)
![Languages](https://img.shields.io/badge/languages-English%20%7C%20Sinhala%20%7C%20Tamil-orange)
![Status](https://img.shields.io/badge/status-proof%20of%20concept-yellow)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

A real time, multilingual voice interaction backend built at SLT Digital
Lab. It evolves the earlier command line prototype into a structured
backend service (FastAPI over WebSocket) with a real frontend, adding
streaming transcription, noise robust voice activity detection, a natural
sounding text to speech voice, and a cloud multi agent reasoning layer that
falls back to a local model when the cloud is not available.

## Architecture

```
Browser microphone
    |  (float32 PCM chunks, streamed over WebSocket)
    v
FastAPI backend (backend/server.py)
    |
    +-> Voice activity detection (backend/vad.py)
    |     WebRTC VAD + a per session ambient noise calibration step
    |
    +-> Streaming speech to text (backend/stt.py)
    |     local faster-whisper (English, and Sinhala/Tamil fallback)
    |     cloud Speech to Text for Sinhala/Tamil when configured
    |
    +-> Chat agent orchestrator (backend/agent.py)
    |     cloud multi agent (reasoning -> tool/retrieval -> composer)
    |     when a cloud LLM is configured and reachable, else local
    |     Llama (via Ollama), streaming its reply token by token as it
    |     is generated (cloud path yields its finished reply as one
    |     chunk; see KNOWN_LIMITATIONS.md)
    |
    +-> Text to speech (backend/tts.py)
          cloud neural voice (Google/Azure) when configured, else a
          local SAPI5 voice for English; Sinhala/Tamil without cloud
          speak a short notice instead of mangling the text

Frontend (frontend/): a single page, no framework, connecting to the
backend over WebSocket. It is a small four screen flow rather than one
bare control panel:

```
Choose your language  ->  Start Chat or Start Voice  ->  Chat screen
(English/Sinhala/Tamil)   (pick how to talk)              (text, via POST /chat)
                                      \
                                       -> Voice screen
                                          (mic permission first, then an
                                          automatic introduction and start,
                                          animated bot avatar, live
                                          captions of its own replies, via
                                          WS /ws/voice; loops turn by turn
                                          until the user sounds satisfied,
                                          then asks a closing question and
                                          ends itself)
```

A theme toggle (top right, persisted across reloads) switches the whole
app between light and dark, and the background is a gradient that both
slowly drifts on its own and softly follows the cursor.

See ARCHITECTURE.md for how the cloud multi agent design and its local
fallback actually work, and KNOWN_LIMITATIONS.md for an honest account of
what is genuinely unsolved right now versus what only needs a credential.

## Quick start

```
git clone https://github.com/BhanukaJanappriya/voice-interaction-layer.git
cd voice-interaction-layer
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull llama3.2:latest
copy .env.example .env
scripts\start.ps1
```
`scripts\start.ps1` starts both the backend and the frontend as background
processes (no console window, output goes to `logs\*.log`), then open
`http://127.0.0.1:5500/index.html`. Run `scripts\stop.ps1` when you are
done. The sections below explain each step and what is optional, plus how
to run each piece directly in a terminal if you want to watch its output
while developing.

## Setup

1. Python 3.12 virtual environment. Python 3.12 was chosen over newer
   system Python versions because faster-whisper's dependencies did not
   support them at the time this was built:
   ```
   python -m venv venv
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Ollama with `llama3.2:latest` pulled and the server running at
   `http://localhost:11434` (unchanged from the prototype; this is the
   local fallback every other path degrades to, so it should always be
   available):
   ```
   ollama pull llama3.2:latest
   ```

3. Copy `.env.example` to `.env` and fill in whichever cloud credentials
   you actually have (Gemini or OpenAI for the LLM, Google Cloud or Azure
   for speech). None of these are required to run the app; see
   KNOWN_LIMITATIONS.md for exactly what each one unlocks.

4. The first run of the STT module downloads the faster-whisper model
   (about 142 MB for "base") from Hugging Face and caches it locally.
   Needs internet once; every run after that is fully offline for the
   local path.

## Running

**Normal use: background, no terminal to watch.**
```
scripts\start.ps1
```
This launches the backend and frontend with `pythonw.exe` (Python with no
attached console window), so neither one occupies a terminal or prints
into one; each writes to its own log file under `logs\` instead
(`logs\backend.log`, `logs\frontend.log`). The script itself returns
immediately once both are started. Stop them with:
```
scripts\stop.ps1
```
Open `http://127.0.0.1:5500/index.html`; the app always starts on the
language screen, then lets you choose Start Chat or Start Voice.

**Development: run a piece directly, to watch its own output live.**

Standalone module tests (verify each piece works in isolation, exactly as
the prototype's modules did):
```
cd backend
..\venv\Scripts\python.exe vad.py      # noise floor calibration + before/after comparison
..\venv\Scripts\python.exe stt.py      # streaming transcription in a chosen language
..\venv\Scripts\python.exe agent.py    # cloud path if configured, plus a forced local path
..\venv\Scripts\python.exe tts.py      # sample sentence in English, Sinhala, Tamil
```

Backend in the foreground:
```
cd backend
..\venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000
```
`GET http://127.0.0.1:8000/health` reports which cloud providers are
actually configured right now; `POST http://127.0.0.1:8000/chat` with a
JSON body of `{"text": "...", "language": "en"}` drives the text chat
screen directly, without a browser.

Frontend in the foreground (any static file server works; this is a plain
HTML/CSS/JS page):
```
cd frontend
..\venv\Scripts\python.exe -m http.server 5500
```

## Why these tools were chosen

**Backend: FastAPI + WebSocket.** The standard, well documented approach
for low latency, bidirectional audio streaming in Python, and keeps the
whole backend in one language and one process model.

**Speech to text: faster-whisper locally, cloud Speech to Text for
Sinhala/Tamil when configured.** faster-whisper remains fast and reliable
offline for English. It is also multilingual out of the box, so it can
attempt Sinhala and Tamil without any extra setup, but its accuracy on
those two languages is materially lower than on English (see
KNOWN_LIMITATIONS.md); Google Cloud Speech to Text has explicit `si-LK`
and `ta-IN` language support and is the documented real fix.

**Voice activity detection: WebRTC VAD + ambient calibration, replacing
the prototype's fixed RMS threshold.** A pretrained classifier that looks
at spectral shape is more robust to steady background noise than a single
loudness threshold, and calibrating to the room at session start (rather
than using one fixed number for every room) is what actually makes this
noise robust rather than just differently tuned. See backend/vad.py for
the standalone before/after test.

**Text to speech: configured cloud provider, then edge-tts, then local
SAPI5 for English only.** A natural sounding voice, especially a specific
gender and tone, is a neural TTS feature; no offline engine on this
machine gets close. Rather than that meaning Sinhala and Tamil (and a
natural male voice) strictly need a paid cloud account, `speak_text()`
tries edge-tts before the local fallback: an unofficial but very widely
used library that calls Microsoft Edge's own text to speech backend (the
same neural voice catalog Azure Cognitive Services uses) with no API key,
account, or credentials, only internet access. Verified live for English,
Sinhala, and Tamil, both a female and a male voice, with no cloud provider
configured at all. The configured cloud provider (Google Cloud Chirp3-HD,
its newest and most natural tier, where available, or Azure Neural) is
still tried first when real credentials are set, since it
is the higher quality, quota backed, officially supported option; edge-tts
is what makes a genuinely natural voice available with zero setup, not a
replacement for a real account in production. See KNOWN_LIMITATIONS.md for
the one caveat: edge-tts is an unofficial, undocumented endpoint that
could change without notice.

**Chat agent: cloud multi agent when configured, local Llama via Ollama
otherwise.** Local Ollama remains the zero cost, zero credential default
so the base demo always works. When a cloud provider (Gemini or GPT) is
configured and reachable, the Orchestrator runs a small three role
collaboration (reasoning, tool/retrieval, composer) against that provider
instead; see ARCHITECTURE.md for why this counts as genuine multi agent
collaboration within this pass's scope.

**The agent's reply streams to the frontend as it is generated, on the
local path.** `LlamaOllamaAgent.respond_stream()` calls Ollama with
`stream: true` and yields each token chunk as Ollama produces it, instead
of `respond()`'s single blocking call that waits for the whole reply.
`server.py` forwards each chunk over the WebSocket, so the transcript
shows the reply being composed live during the "thinking" status, not
only once it is fully ready, which is what "real time" should mean for a
step that can otherwise take several seconds. Verified against the real
local Ollama server: a short prompt produced two separate WebSocket
messages ("Hello", then "!"), not one. This is not implemented for the
cloud multi agent path or the text chat screen's `POST /chat`; see
KNOWN_LIMITATIONS.md for exactly why and what each would need.

**Frontend flow: language, then chat or voice, as separate screens.**
Putting language selection first and a chat versus voice choice second,
instead of one screen with every control on it, keeps each screen doing
one thing and matches how a real product in this space is normally
structured. The text chat screen reuses the exact same `Orchestrator` the
voice pipeline uses through a small `POST /chat` endpoint (see
`backend/server.py`), so cloud versus local fallback behavior, and which
mode answered, are identical in both screens; it just skips STT and TTS
entirely, since text in and text out needs neither.

**Between that choice and the actual screen: a new or existing user
check, then a greeting.** Existing users are asked for a name (remembered
locally, see KNOWN_LIMITATIONS.md for why this is a name to be addressed
by, not real authentication); new users are addressed as Guest. Either
way, landing on the voice or chat screen triggers an immediate,
personalized introduction rather than a blank waiting screen, and the
voice screen additionally shows an explanatory modal before the browser's
own native microphone permission prompt, so that prompt does not appear
out of nowhere.

**The voice screen starts itself, with no button to press.** Entering it
shows the mic permission explainer (or, if the browser already reports
permission as granted, skips straight past it); once permission is
settled, `beginVoiceSession()` (`frontend/app.js`) speaks the
introduction ("Welcome to SLT Mobitel, ..."), waits for it to finish, then
opens the WebSocket and starts listening automatically. The screen itself
is down to five controls: conversation history (top left), logout and the
theme toggle (top right), and captions on/off, the mic, and an end chat
button along the bottom, rather than a status row, badges, and a manual
start button. Captions mirror only the assistant's own current line, never
the user's own live speech transcript, so they read as subtitles for what
the assistant is saying; the full back and forth is still in the history
panel regardless of whether captions are on.

**The conversation loops on its own until the user sounds done, then asks
before ending.** Each turn already stops listening the moment the user
stops talking (voice activity detection, see below) and starts listening
again once the reply has been spoken; `backend/server.py`'s
`_sounds_like_closing_signal()` additionally watches each final transcript
for a satisfied/thankful phrase ("thank you", "that's all", and a couple
of common Sinhala/Tamil equivalents). The first time one is heard, the
assistant asks a canned closing question instead of routing that turn to
the agent; if the next thing the user says also reads as closing (or a
plain "no"), the assistant says goodbye, the frontend receives an
`end_chat` event, and the session actually ends, back at the Start Chat/
Start Voice screen. If instead the user asks something new, the closing
offer is simply dropped and that message is answered normally, so a
"thank you" that turns out to be the middle of a sentence rather than a
goodbye does not cut the conversation off. The end chat (cut) button on
the bottom bar does the same ending immediately, for when the user wants
to stop without going through that exchange.

**The assistant's own speech cannot be mistaken for the next thing the
user says.** On a single machine demo, the reply plays on that same
machine's speakers, so a microphone left open the whole time could pick
up its own voice. `frontend/app.js` only forwards captured audio to the
backend while the server reports it is actually listening or calibrating,
never while it is thinking or speaking, and `server.py` runs `speak_text()`
in a worker thread rather than inline, so its blocking playback cannot
stall the event loop and delay that status change for every connection.

**Every icon is an inline SVG using `currentColor`, not emoji.** A stroke
only icon set means every icon picks up the surrounding element's own
color (the Mobitel blue almost everywhere, or a button's own white text
color when it sits inside a solid colored button) with no extra styling
per icon, keeping the palette restricted rather than mixing in whatever
colors the visiting platform's emoji font happens to render.

**Voice screen avatar: a real 3D face (Three.js/WebGL), with a flat 2D
fallback.** A genuinely three dimensional, lit face needs an actual 3D
renderer; CSS can fake perspective but not real per pixel lighting and
shading. `frontend/bot3d.js` renders one, whose mouth animates paced to
the actual words of each reply while the state-driven flat CSS avatar
(`style.css`'s `.bot` rules) keeps working underneath as an automatic
fallback if WebGL is unavailable. See KNOWN_LIMITATIONS.md for why this
is text paced rather than true audio driven lip sync, and what a real fix
would involve.

## Current limitations

See KNOWN_LIMITATIONS.md for the full, honest list. In short: Sinhala and
Tamil natural voice output and high accuracy transcription both require
cloud credentials that are not configured on this machine right now; the
"streaming" transcription is chunk based re-transcription, not a natively
streaming ASR model; and the multi agent cloud path has not been exercised
against a live Gemini/GPT account in this pass, only its local fallback
(which has been, repeatedly, including with automated end to end tests).

## Suggested next steps

- Provide Gemini or GPT, and Google Cloud or Azure Speech credentials, then
  re-run the standalone tests for agent.py, stt.py, and tts.py to confirm
  the cloud paths end to end, not just their fallback behavior.
- Stream synthesized audio back to the browser for in-page playback,
  instead of the current design where the reply plays on the server
  machine's own speakers.
- Move the frontend's audio capture from ScriptProcessorNode (deprecated)
  to an AudioWorklet.
- Add conversation history so the chat agent has multi turn context; both
  the prototype and this pass are stateless per turn.
