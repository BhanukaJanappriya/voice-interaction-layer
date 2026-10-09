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

One FastAPI process (`python -m backend`) is the whole app: it serves the
frontend, the admin portal, the voice WebSocket, the APIs and the MCP
server, with one config, one database and one startup sequence. Inside it
stays modular: each part below is its own module or package with its own
interface (see INTERFACES.md).

```
Browser microphone
    |  (float32 PCM chunks, streamed over WebSocket)
    v
FastAPI backend (backend/server.py, started by python -m backend)
    |
    +-> Voice activity detection (backend/vad.py)
    |     WebRTC VAD + a per session ambient noise calibration step
    |
    +-> Streaming speech to text (backend/stt.py)
    |     local faster-whisper (English, and Sinhala/Tamil fallback),
    |     biased toward "SLT Mobitel" with hotwords, no noise filter (it made
    |     Whisper worse), Whisper's own speech filter, and a larger model that
    |     rechecks a final transcript the main one is unsure of
    |     cloud Speech to Text for Sinhala/Tamil when configured
    |
    +-> Chat agent orchestrator (backend/agent.py)
    |     1. an external chat agent, if an admin made one active
    |        (backend/admin, ExternalHttpAgent), else
    |     2. cloud multi agent (tool/retrieval -> composer, streamed)
    |        when a cloud LLM is configured and reachable, else
    |     3. local Llama (via Ollama), streaming token by token
    |     The tool/retrieval step is the SLT Mobitel knowledge base
    |     (backend/knowledge): bge-m3 embeddings, official sources only.
    |
    +-> Text to speech (backend/tts.py)
          cloud neural voice (Google/Azure) when configured, else
          edge-tts, else a local SAPI5 voice for English

Also in the same process:
    /auth        existing user verification (landline + PIN), admin sign in (backend/auth)
    /admin/      admin portal page; /admin/api its API (backend/admin)
    /mcp         MCP server for other agents, OAuth client credentials (backend/mcp_server)
    /health      providers, database, knowledge index, active external agent, MCP
    one database SQLite by default, PostgreSQL by DATABASE_URL (backend/db, Alembic)
```

Frontend (frontend/): a single page, no framework, served by the same
process and connecting to it over WebSocket. It is a small four screen flow
rather than one bare control panel:

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
ollama pull bge-m3
copy .env.example backend\.env
venv\Scripts\python.exe -m backend.knowledge.build
venv\Scripts\python.exe -m backend.db.seed
scripts\start.ps1
```
`scripts\start.ps1` starts the app as one background process (no console
window, output goes to `logs\backend.log`), then open
`http://127.0.0.1:8000/`. Run `scripts\stop.ps1` when you are done. Set
`ADMIN_PASSWORD` in `backend\.env` before the first start to get an admin
login for `http://127.0.0.1:8000/admin/`. The knowledge base build crawls
two official sites politely (about 20 minutes the first time, incremental
after that); the seed adds three fictional demo users. The sections below
explain each step and what is optional, plus how to run each piece
directly in a terminal if you want to watch its output while developing.

## Setup

1. Python 3.12 virtual environment. Python 3.12 was chosen over newer
   system Python versions because faster-whisper's dependencies did not
   support them at the time this was built:
   ```
   python -m venv venv
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Ollama with `llama3.2:latest` pulled and the server running at
   `http://127.0.0.1:11434` (the backend uses the IPv4 address rather than
   `localhost`, which costs about 2 seconds per request on Windows; this is the
   local fallback every other path degrades to, so it should always be
   available):
   ```
   ollama pull llama3.2:latest
   ```

3. Copy `.env.example` to `backend\.env` and fill in whichever cloud
   credentials you actually have (Gemini or OpenAI for the LLM, Google
   Cloud or Azure for speech). None of these are required to run the app;
   see KNOWN_LIMITATIONS.md for exactly what each one unlocks. Secrets
   (`JWT_SECRET`, `FIELD_ENCRYPTION_KEY`) may stay empty on a development
   machine: a random value is generated once and kept in
   `data\secrets.json`. Set real values anywhere shared.

4. The first run of the STT module downloads the faster-whisper model
   (about 142 MB for "base") from Hugging Face and caches it locally.
   Needs internet once; every run after that is fully offline for the
   local path.

5. The multilingual embedding model for the knowledge base, served by the
   same Ollama, then the knowledge base itself:
   ```
   ollama pull bge-m3
   venv\Scripts\python.exe -m backend.knowledge.build
   ```
   The database needs no setup: migrations run at startup.

## Running

**Normal use: background, no terminal to watch.**
```
scripts\start.ps1
```
This launches the app with `pythonw.exe` (Python with no attached console
window), so it does not occupy a terminal or print into one; it writes to
`logs\backend.log` instead. The script itself returns immediately once the
process is started. Stop it with:
```
scripts\stop.ps1
```
Open `http://127.0.0.1:8000/`; the app always starts on the language
screen, then lets you choose Start Chat or Start Voice. The admin portal
is at `http://127.0.0.1:8000/admin/`.

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

The whole app in the foreground (one process, the same one start.ps1 runs):
```
venv\Scripts\python.exe -m backend
```
`GET http://127.0.0.1:8000/health` reports which cloud providers are
actually configured right now, plus the database, the knowledge index,
the active external agent and the MCP server; `POST
http://127.0.0.1:8000/chat` with a JSON body of `{"text": "...",
"language": "en"}` drives the text chat screen directly, without a
browser.

The old split setup still works for frontend work: serve `frontend\` with
`..\venv\Scripts\python.exe -m http.server 5500` from inside `frontend\`
and the page talks to the backend at `127.0.0.1:8000` (with a bearer token
instead of the session cookie, since it is another origin).

Tests (no microphone, cloud keys or network needed; the `live` ones need
Ollama and the built knowledge base):
```
venv\Scripts\python.exe -m pytest -m "not live"
venv\Scripts\python.exe -m pytest -m live
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
collaboration (tool/retrieval, then a streamed composer, with an optional
reasoning stage via `CLOUD_REASONING_STAGE`) against that provider
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
check, then a greeting.** Existing users are verified: they enter their
landline number and a PIN, inside the same panel the name field used to
be in, and the backend checks both against the customer database (see
"Existing user verification" below). The assistant then addresses them by
the name on their account. New users are addressed as Guest, exactly as
before. Either way, landing on the voice or chat screen triggers an immediate,
personalized introduction rather than a blank waiting screen, and the
voice screen additionally shows an explanatory modal before the browser's
own native microphone permission prompt, so that prompt does not appear
out of nowhere.

**The voice screen starts itself, with no button to press.** Entering it
shows the mic permission explainer (or, if the browser already reports
permission as granted, skips straight past it); once permission is
settled, `beginVoiceSession()` (`frontend/app.js`) opens the WebSocket
straight away, and the backend speaks the introduction out loud ("Welcome
to SLT Mobitel, ...", by name or as Guest) as the first thing in the
session, then calibrates and starts listening automatically. Text to
speech is on by default; there is no switch to turn it on first. The
agent keeps using the name in its own replies, and a user can also say
"my name is ..." mid conversation. The screen itself
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

## SLT Mobitel knowledge base (retrieval, not fine tuning)

The assistant answers SLT Mobitel questions (packages, prices, data
bundles, roaming, FAQs, support, the company) from a local knowledge base
instead of from what the model happens to remember, which before this
meant invented package names. Retrieval rather than fine tuning because
prices and packages change: rebuilding the index takes one command, and
every answer can be traced to a source page and its retrieval date.

```
venv\Scripts\python.exe -m backend.knowledge.build                 crawl (incremental), normalize, index
venv\Scripts\python.exe -m backend.knowledge.build --skip-scrape   rebuild from the cached pages only
venv\Scripts\python.exe -m backend.knowledge.eval                  measure it, writes docs/RAG_EVAL.md
```

- **Sources.** The official sites www.sltmobitel.lk and www.mobitel.lk
  (the same company's older site, needed because the new site loads its
  package tables in the browser, see docs/RAG_EVAL.md), crawled politely:
  robots.txt obeyed, a clear User Agent, at least 1.5 s between requests
  (10 s where robots.txt asks), no login, cart or account pages. Plus a
  committed, hand checked file of public sources (Wikipedia, official
  press releases, TRCSL) with a source URL on every entry, and a curated
  FAQ of 59 question and answer pairs. Raw pages and the index live under
  `data\` and are never committed.
- **Embeddings and store.** The pages are cut into focused chunks of about
  160 tokens (the original plan said 300 to 500; bigger chunks buried the
  answer, see docs/RAG_EVAL.md), embedded with bge-m3 through the local
  Ollama: multilingual, so Sinhala and Tamil questions find English
  content. Vectors are a numpy matrix searched with one matrix product
  (well under a millisecond for about 2,200 chunks); see the ADRs in
  docs/IMPLEMENTATION_PLAN.md. Ranking adds a small keyword boost (a
  question word found in a chunk's text or title) because meaning alone
  blurs exact words like "email" or "hotline"; relevance is still judged on
  the plain embedding score.
- **How it reaches the answer.** The retriever is the Orchestrator's
  existing `tool_callback` hook. On the cloud path it is the tool/retrieval
  stage of the multi agent design; on the local path the retrieved text
  goes into the Llama system prompt, which is plain context for one model,
  not multi agent collaboration. Two short prompts (knowledge/prompts.py),
  chosen by code rather than left to the model: when retrieval found notes,
  the assistant answers only from them, copies prices, codes and numbers
  exactly, and points to www.sltmobitel.lk or 1717 when the notes do not
  cover the question; when it found none (general knowledge, how things
  work, advice, small talk) it answers naturally from the model's own
  knowledge and says so honestly when unsure, but never makes up
  SLT Mobitel's own prices, packages, codes or contact details. It does not
  state other operators' prices or packages, since it has none, and
  suggests that operator's own website. Only the best three chunks, each
  cut to the sentences that match the question, go to the model, and Ollama
  runs with a low temperature and bounded length and context.
- **Measured.** Right source in the top 4 for 100% of 32 test questions;
  expected facts in the local model's English answers went from 6% to 79%;
  retrieval takes 75 ms p50 and 105 ms p95 on its own, embedding call
  included. Details, per question results and the Sinhala/Tamil caveats
  are in docs/RAG_EVAL.md.

## Existing user verification

"Existing user" is a real check now, not a typed name:

- The landline number is validated as a Sri Lankan landline (valid area
  code, 0XX XXXXXXX, +94 or 0094 forms) and normalized to E.164 before the
  lookup; mobile numbers are refused.
- A number alone is public information (directories, bills), so it only
  says who the user claims to be. A PIN chosen at registration (4 to 6
  digits, stored only as a bcrypt hash) confirms it. Landlines cannot
  receive SMS, so an SMS one time code was not an option.
- Five wrong PINs in a row lock the account for 15 minutes, and each
  client address gets at most 10 attempts per minute. A wrong number and a
  wrong PIN get the same answer, so the check does not reveal who is a
  customer.
- On success the backend sets an httpOnly session cookie (and returns a
  short lived token for the separate port 5500 development page). The
  voice session and `POST /chat` take the user's name from that verified
  session on the server, not from what the browser sends.

Users are added with
`venv\Scripts\python.exe -m backend.auth.manage_users add --phone "011 234 5678" --name "Name"`
(the PIN is asked for without echo); `list`, `unlock` and `set-pin` also
exist, and `admin-password` resets an admin's password and clears its
lockout (the ADMIN_PASSWORD in .env only seeds the very first admin).
`python -m backend.db.seed` adds three fictional demo users and prints
their demo PINs.

## Admin portal and external chat agents

`http://127.0.0.1:8000/admin/`, styled with the main app's own stylesheet
and theme toggle. The first admin is created at startup from
`ADMIN_USERNAME` and `ADMIN_PASSWORD` when no admin exists yet.

An admin can register another chat agent by URL (method, an auth header
whose value is stored encrypted and never shown again, a JSON body
template such as `{"message": "{text}"}`, the JSON path of the reply text,
a timeout, optional Server Sent Events streaming), test it with a sample
message, and make one agent active. While one is active, every user turn
goes to it and its text reply is spoken by this project's own pipeline
(sentence chunking, streaming TTS, the 3D avatar and its events), with the
mode badge showing "external agent (name)". If it fails before saying
anything, the built in assistant answers that turn instead.

Outbound calls are guarded against server side request forgery: http and
https only, every resolved address must be public (private, loopback,
link local, metadata and similar addresses are refused, checked again at
connect time against DNS rebinding), no redirects, a response size cap
and strict timeouts. `EXTERNAL_AGENT_ALLOW_PRIVATE=true` lifts the
address check for local development only.

The same portal creates and revokes MCP clients and shows the MCP audit log.

## MCP server

Other agents can use this assistant over MCP (streamable HTTP) at
`<PUBLIC_BASE_URL>/mcp`. Create a client in the admin portal (MCP
clients) and copy its secret; it is shown once and only a hash is kept.
Scopes: `knowledge:read` (`search_knowledge`, `get_package_details`) and
`assistant:ask` (`ask_assistant`).

Get a token (valid `MCP_TOKEN_MINUTES`, default 15):
```
curl -u "CLIENT_ID:CLIENT_SECRET" -d grant_type=client_credentials http://127.0.0.1:8000/oauth/token
```
Then call `/mcp` with `Authorization: Bearer <access_token>`. A missing or
invalid token gets 401 with a `WWW-Authenticate` header pointing at
`/.well-known/oauth-protected-resource/mcp`, which is how MCP clients
discover the token endpoint; a tool outside the token's scopes gets 403
`insufficient_scope`. Revoking a client takes effect on its next request.
Every token request and tool call is in the audit log, without any
question or answer text. `PUBLIC_BASE_URL` must be the address clients
actually use: it is the token issuer and audience, and requests with any
other Host header are refused.

## Database

One database for users, admins, external agents, MCP clients and the
audit log: SQLite in `data\app.db` by default, PostgreSQL by setting
`DATABASE_URL` (plus a driver such as `psycopg[binary]`), no code change.
Migrations (Alembic) run at startup; by hand, from the repository root:
```
alembic upgrade head            (or: venv\Scripts\python.exe -m backend.db.migrate)
alembic revision --autogenerate -m "what changed"    after changing backend/db/models.py; review it, then alembic check
```

## Browser audio, a new avatar, a reusable voice and hub login

Added on top of everything above; the original `/ws/voice` path, its server
side playback and the old avatar still work and can be selected in `.env`.

**Real time audio in the browser (LiveKit).** With `VOICE_TRANSPORT=livekit`
(the default) the page joins a LiveKit room, publishes the microphone with the
browser's echo cancellation and noise suppression, and plays the assistant's
voice in the page. The same VAD, speech to text and Orchestrator run behind it,
and the assistant stops talking when you start to speak. Set `LIVEKIT_URL`,
`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in `.env` (LiveKit Cloud, or a local
server: download `livekit-server` from the LiveKit releases page and run
`livekit-server --dev`, keys `devkey` and `secret`). Without them the page uses
`/ws/voice` as before. Set `VOICE_TRANSPORT=websocket` to force the old path.

**New avatar.** With `AVATAR_RENDERER=talkinghead` (the default) the voice
screen shows a TalkingHead avatar (MIT) whose mouth follows the audio that
reaches the browser, with blinking, gaze, mood and gestures tied to what the
assistant is doing. Download its model once:

    python frontend/avatar/fetch_assets.py

A male and female switch in the top bar changes the avatar and the voice
together and is remembered. The male model is built, not downloaded
(`frontend/avatar/tools/MALE_AVATAR.md`); without it only the voice changes.
If WebGL or a model file is missing, the page shows the old avatar instead.
`AVATAR_RENDERER=legacy` keeps the old one always.

**voicekit.** `backend/voicekit` lets any text generating agent speak: a Python
adapter, `POST /api/tts/stream` and `WS /ws/tts` (API keys in
`VOICEKIT_API_KEYS`), and a small browser client with word timings for lip
sync. Quickstart and three examples are in docs/VOICEKIT.md.

**MCP hub.** The assistant is an MCP client of https://mcphub.sltdigitallab.lk
(`MCP_HUB_*` in `.env`; the key stays there). `/health` shows `mcp_hub`. Its
slow knowledge search is off the spoken reply path by default, and tools that
buy or change things are never exposed to the model.

**One time code login.** Existing users can sign in with a code instead of a
PIN: `AUTH_METHODS=pin,otp`. The hub sends the code when it is reachable
(`OTP_BACKEND=auto`), otherwise a local code goes to the mobile number or email
stored for the customer (`OTP_SENDER=console|email|sms`). Codes are six digits,
valid 5 minutes, three attempts, with a resend cooldown and rate limits per
number and per address. The PIN login is unchanged and Guests are unaffected.

**Order of attempts.** A voice conversation tries LiveKit first. The page asks
`GET /api/livekit/config`; it joins a room only when the server is set to
`livekit`, has its keys and reports `agent_ready` (the voice agent is running).
If any of that is false, if the token is refused, if the connection fails or if
no agent answers within 20 seconds, it opens `/ws/voice` instead, and the reply
is generated by the same Orchestrator: Gemini when a key works, otherwise the
local Ollama model alone.

**Which model writes the reply.** LiveKit carries the audio; the words come from
the Orchestrator. With `CLOUD_LLM_PROVIDER=livekit` (the default) it calls
LiveKit Inference, LiveKit Cloud's hosted model gateway, with the same
`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` (no other model key; the model is
`LIVEKIT_LLM_MODEL`, `openai/gpt-4o-mini` by default). The mode badge then reads
"cloud multi agent (livekit)". Only when that is not set up, has no credit or
fails does the local Ollama model answer alone, shown as "local single agent
(Llama via Ollama)", and LiveKit is skipped for 5 minutes before it is tried
again. `/health` shows `cloud_llm_provider`, `cloud_llm_model` and
`cloud_llm_last_error`. `CLOUD_LLM_PROVIDER=gemini` or `gpt` still work.

**Still seeing the old bot, or the male and female switch only changes the
voice?** `.env` values beat the defaults in the code. `backend/.env` copied from
an older template can still say `VOICE_TRANSPORT=websocket` and
`AVATAR_RENDERER=legacy`; `/health` then shows `"avatar_renderer": "legacy"` and
`"livekit": {"transport": "websocket", "worker": null}`. Set `livekit` and
`talkinghead`, run `python frontend/avatar/fetch_assets.py` once, and restart the
app (settings are read at startup). The male avatar needs
`frontend/avatar/assets/male.glb`; without it only the voice switches.

Documents: docs/IMPLEMENTATION_PLAN.md (Part 2, with the decisions),
docs/RESEARCH.md and docs/THIRD_PARTY.md (every asset, licence and source),
docs/VOICEKIT.md, and the "not live tested" list in KNOWN_LIMITATIONS.md.
Measure the first word yourself with
`python scripts/measure_first_audio.py --runs 8` while the app runs.

## Current limitations

See KNOWN_LIMITATIONS.md for the full, honest list. In short: Sinhala and
Tamil natural voice output and high accuracy transcription both require
cloud credentials that are not configured on this machine right now; the
"streaming" transcription is chunk based re-transcription, not a natively
streaming ASR model; and the multi agent cloud path has not been exercised
against a live Gemini/GPT account in this pass, only its local fallback
(which has been, repeatedly, including with automated end to end tests).
Scraped prices go stale until the knowledge base is rebuilt, the local
model answers Sinhala and Tamil questions poorly even with the right
context, grounded replies take about 0.3 s longer to start speaking, and
the MCP server, PostgreSQL and a real HTTPS external agent were built and
tested locally but not against real third party clients or servers.

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

## Reply fallback chain, guest access and the verification wizard

Added 2026-10-09. Design and decisions: docs/IMPLEMENTATION_PLAN.md Part 3.

**Reply models.** Each turn tries Gemini, then LiveKit Inference, then the local
Llama model, in the order of `LLM_CHAIN` (default `gemini,livekit,local`). A tier
counts as unavailable for a missing key, a refused key (401 or 403), used up
credit or quota (402 or 429), an unknown model (404), a provider outage (5xx), a
timeout or a network failure. Each tier has its own 5 minute breaker and is
probed once at startup. A tier that fails restarts the whole turn on the next
one. A safety block or refusal from a provider is a valid answer and is not
retried elsewhere; the user hears a short translated notice instead. `/health`
shows `llm_chain` with every tier's state and last error reason, and the mode
badge shows the tier that answered, for example `cloud (gemini)`,
`livekit inference (openai/gpt-4o-mini)` or `local single agent (Llama via
Ollama)`. Setting `LLM_CHAIN=` (empty) goes back to the single provider chosen
by `CLOUD_LLM_PROVIDER`. The text `/chat` path uses every tier too, because
LiveKit Inference is called from plain server code here.

**Guests and verified users.** A guest (chose New user, or did not finish the
one time code) can chat and get public information. A request that needs
customer data (account, bills, payments, connection status, faults, package
changes, personal usage) is not sent to any model. The reply is a fixed,
pre translated message giving 1212, SMS 0113121212 and 1222 for prepaid, and
saying they can verify as an existing user. The topics, keywords and numbers
are in `backend/access/policy.json`, which the supervisor can edit without
code changes (it reloads when the file changes). Hub tools that need a customer
are refused on the server unless the session finished the one time code, and a
verified user can only use their own number. Turn it off with
`ACCESS_CONTROL_ENABLED=false`.

**Verification wizard.** Existing user opens three modal steps (details,
where to send the code, enter the code), in English, Sinhala or Tamil. The
server checks everything: `POST /verify/lookup`, `/verify/otp/send`,
`/verify/otp/verify`, `GET /verify/session`, `POST /verify/logout`. Codes are 6
digits, kept only as an HMAC, valid 120 seconds, single use, 3 attempts, 30
second resend cooldown, with limits per address and per number. Customers come
from the `customer_directory` table, because the MCP hub has no lookup tool
and cannot send a message on its own. Put fake test rows in with
`cd backend; python -m verification.seed_demo`. In development
`OTP_SENDER=console` prints the code in the server window; real delivery needs
`SMTP_*` or `SMS_GATEWAY_*` in `.env`.

**Languages.** Wizard text lives in `frontend/i18n/{en,si,ta}.json`, server
messages in `backend/i18n/{en,si,ta}.json`. The Sinhala and Tamil wording is
machine written and needs review by a native speaker.
