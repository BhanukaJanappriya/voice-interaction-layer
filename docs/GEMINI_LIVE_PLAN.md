# Gemini Live plan

Written 2026-10-15 before any code. Status words: **verified** (seen working here), **docs** (read in the vendor's documentation, not run), **unverified** (an assumption).

## 0. Phase 0 findings

| Check | Result |
|-|-|
| Git | `main` tracks `origin/main` (github.com/BhanukaJanappriya/voice-interaction-layer). Identity `Bhanuka Janappriya`. Commit style: a plain sentence subject, a prose body, no trailers, no dashes. Unrelated working tree changes (`scripts/demo_external_agent.py`, `tests/test_orchestrator.py`, the Sinhala overview docx) are not ours and are never staged. |
| Model list (verified, key from `backend/.env`) | 63 models. Live capable (`bidiGenerateContent`): `gemini-3.8-live`, `gemini-3.8-live-extended-thinking`, `gemini-3.1-flash-live-preview`, `gemini-2.5-flash-native-audio-latest` and two dated 2.5 previews, `gemini-3.5-live-translate-preview`, `gemini-3.5-transcribe-live`. TTS: `gemini-3.8-flash-tts`, `gemini-3.8-flash-lite-tts`. |
| Model docs (docs) | `gemini-3.8-live` is the default for low latency voice agents. `gemini-3.8-live-extended-thinking` is for high reasoning. `gemini-3.1-flash-live-preview` and the 2.5 native audio models are legacy. |
| Model in `backend/.env` | `GEMINI_MODEL=gemini-3.8-live`. That value is a Live model; the text chain tier (`GeminiAgent`, `generateContent`) cannot use it. `.env.example` still says `gemini-flash-lite-latest`. The fix is a separate setting, `GEMINI_LIVE_MODEL`, and a text model for `GEMINI_MODEL` (ADR-1). |
| Live test session (verified) | Connect with `gemini-3.8-live`, `gemini-3.1-flash-live-preview` and `gemini-2.5-flash-native-audio-latest`: all three rejected at setup with close code 1011, "Your prepayment credits are depleted". **No audio was ever received, so connect time and first audio latency could not be measured.** The key is valid (the model list works); it has no credit. Per the fallback rules this is a genuine reason to fall back (billing, 402 class). |
| SDK | `google-genai 2.25.0` installed in the venv. |

Consequence: Gemini Live is built against the SDK's documented behaviour and tested with a scripted fake Live server. It is **not** live tested until the owner tops up the key. Everything the brief asks to be measured on Gemini (first audio latency, voice listening, mouth offset on Gemini audio) is reported as not measured.

## 1. Architecture

```
browser mic ──PCM16 16 kHz──▶ /ws/live (backend proxy) ──▶ Gemini Live (google-genai, key stays here)
browser ◀──PCM16 24 kHz + JSON events── backend ◀── audio, transcripts, tool calls
                                          │ tool call ──▶ mcp_gateway.call(name, args, access)   (same matrix, same confirmations)
TalkingHead lip sync ◀── the same PCM chunks (avatar.speakAudio), HeadAudio visemes
```

**Audio path (ADR-2): backend WebSocket proxy, not LiveKit and not a direct browser connection.** Reasons: tool calls must run through the gateway on the server, so the server has to sit in the session anyway; the key never leaves the server and no ephemeral token endpoint is needed (an ephemeral token would let the browser talk to Gemini with tools the gateway never saw); the existing `/ws/voice` already carries PCM and JSON events, so the page code for events is reused. The LiveKit room stays the transport for tier 2. Ephemeral tokens (`client.auth_tokens.create`, docs) are **not** used; the plan records how they would be minted if the owner later wants a direct mode, and says that mode would need the tool calls relayed back through the server.

Formats (docs): input raw PCM16 little endian 16 kHz, output PCM16 24 kHz. The page already produces 16 kHz mono float32; the proxy converts to int16.

## 2. Session configuration

Per session, built server side:

* `response_modalities=["AUDIO"]`, `input_audio_transcription` and `output_audio_transcription` on, so the chat transcript keeps working.
* `speech_config.voice_config.prebuilt_voice_config.voice_name` from `config/voices.yaml` by (language, gender). Native audio models take no language code (docs), so the language is in the system instruction.
* System instruction: the same composer persona as the other tiers plus a language block ("Always reply in Sinhala. Speak naturally, Sri Lankan pronunciation of names and places, read rupee amounts as rupees and phone numbers digit by digit, say Fibre, data bundle and PEOTV as English words"), the contact rule, and the user tier rules ("you are speaking to a guest: you may only use the knowledge tool; for anything personal, ask them to verify").
* `tools=[function_declarations]` from `mcp_gateway.tool_schemas(access, service_type, include_state_changing=<confirmations on>)`. The model never sees a tool the gateway would refuse. The telephone number and the token are never declared as parameters.
* `session_resumption` with the latest handle, `context_window_compression` (sliding window) so audio sessions are not capped at 15 minutes (docs), `realtime_input_config` with automatic activity detection (barge in is the default behaviour).

Limits (docs): audio only sessions 15 minutes without compression; a connection lives about 10 minutes; a `GoAway` message carries `timeLeft`; resumption handles are valid 2 hours. Plan: compression on, and on `GoAway` (or a connection drop) reconnect with the stored handle at the next gap between turns, audio buffered for at most 1 second meanwhile.

## 3. Tool calling

`GeminiLiveSession` receives a `ToolExecutor` (a callable the server builds around the user's `AccessContext`). A function call becomes `executor(name, args) -> dict`, which calls `mcp_gateway.call` on a worker thread and returns the hub's text wrapped as `{"result": ...}` or `{"error": reason}`. The model's arguments are cleaned by the gateway (number forced, unknown tools refused, tier checked). State changing tools: the executor calls `confirmations.propose(...)` and returns `{"status": "needs_confirmation", "say": "..."}`; the page shows the same modal, and a spoken or clicked yes executes with the one time token (60 s). A guest's session simply has one declaration. The result cap and the PII free audit are the gateway's.

Grounding: the instruction tells the model to answer SLT facts only from tool results, and the existing output guard (`tool_agent.scrub`) cannot see native audio, so for tier 1 the transcript text is scrubbed for display and a violation is logged (the audio cannot be recalled). This is a real limit and goes in KNOWN_LIMITATIONS.

## 4. Fallback state machine (`backend/voice_router`)

Tiers: `gemini_live` > `livekit` > `local`.

```
          probe/ok                   fail (genuine)                 fail (genuine)
  [LIVE ready] ────────▶ serve      ───────────────▶ [LIVEKIT]     ───────────────▶ [LOCAL] (last resort, never trips)
       ▲  breaker closes (probe)         breaker open N s                 breaker open N s
       └────────────────── new sessions go back up ───────────────────────┘
```

* Genuine failures only (classifier `voice_router.reasons`): no key, invalid key (401/403), billing or quota (402, 429, "prepayment credits depleted", `RESOURCE_EXHAUSTED`), model unavailable (404), outage (5xx, 1011/1013), setup or connect timeout, and repeated session failure (3 in 5 minutes). **Not** failures: a safety block (`SAFETY`, `prohibited_content`), a refusal, an "I don't know", a user hang up, a normal close (1000), a barge in.
* The next tier is tried only after the previous one failed the check. The same circuit breaker class as `llm_chain.Breaker` is reused, one per tier.
* Probes at startup (cheap: model list call for tier 1, a connect plus setup for tier 2 when credentials exist, the Ollama tags call for tier 3) and at session start (breaker state, then a connect attempt with an 8 second setup budget).
* Mid session failure: finish or cut the current utterance (stop audio at the next chunk boundary), say the localized notice `voice.reconnecting` ("One moment, reconnecting") with the next tier's voice, and continue on the next tier with a `HandoverState` (language, voice gender, `AccessContext`, the last turns as a summary, the pending confirmation). The pending confirmation is held by `confirmations` per customer and survives a tier change by construction; the handover only needs to tell the new tier to mention it.
* Recovery upward: when the tier 1 breaker window ends, the next session start probes again; success closes it and new sessions start on Gemini Live. A running session is not moved back up mid conversation.
* `/health` gets `voice_router: {order, active, tiers: {name: {state, last_error, last_ok_at, retry_in}}}`; logs carry the tier, the reason class and timings only.

Tier 2 (LiveKit with LiveKit Inference LLM, STT and TTS) uses the existing room transport and `LiveKitVoiceSession`; the new part is that STT and TTS can come from LiveKit Inference (`livekit.agents.inference.STT/TTS`) instead of local Whisper and voicekit when `VOICE_TIER2_INFERENCE=true`. Tier 3 is today's pipeline unchanged. The LLM chain (gemini text, livekit, local) stays as it is for text chat.

## 5. Voices

See `docs/VOICE_EVAL.md`. One file, `config/voices.yaml`, maps (tier, language, gender) to a voice name; code reads it through `voicekit`'s existing voice map and the new `voice_router.voices`. The avatar gender and the voice gender come from the same `voice_style` value (`female_natural` | `male`), so they cannot differ on any tier.

## 6. i18n audit plan (R4)

1. Static scan (`tests/test_i18n_hardcoded.py`): regex scan of `frontend/*.js`, `frontend/**/*.js`, `frontend/index.html` text nodes and attributes (`placeholder`, `title`, `aria-label`, `alt`), and `backend/**/*.py` for user facing literals (strings passed to `reply`, `speak`, `detail`, JSON `message`), against an allowlist file with reasons.
2. Key parity test: every key in `en.json` exists in `si.json` and `ta.json`, with non empty values, same placeholders.
3. Browser check: for each of si and ta, load every screen and modal, assert no Latin word of length 4+ outside an allowlist (brand names, units), screenshots at 360 px.
4. New strings listed in `docs/TRANSLATIONS_REVIEW.md` with a column for the native speaker's verdict.

## 7. Avatar plan (R5), DROPPED on 2026-10-16

The owner asked for the previous male and female avatars to stay (`mpfb.glb`, `male.glb`). The Sri Lankan build below was made and then not adopted: nothing in the page uses it and it is not committed. The text is kept as a record of what was planned.

`tools/avatar/build_avatar.py` runs under Blender 5.2 (installed at `C:\Program Files\Blender Foundation\Blender 5.2`): imports the source GLB, keeps armature and shape keys untouched, retints the skin base color texture by a luminance preserving transform toward a preset (not a flat multiply), darkens hair and eyebrows, assigns a black crew neck material to the existing T shirt mesh and projects the official logo (`frontend/assets/mobitel-logo.png`) as a decal on the chest, then exports GLB with WebP textures. Skin presets in `config/avatar.yaml` (default `medium_brown`). Current assets: `male.glb` 19.3 MB, `mpfb.glb` 36.8 MB, far over the 8 MB target, so textures are downscaled and compressed and the result is verified with `validate_glb.py` (shape keys and visemes present) and a lip sync smoke test. Git: the generated GLBs are stored with Git LFS (installed, 3.7.0). Runtime tint fallback in three.js if the Blender route fails.

## 8. File ownership and contracts

| Owner | Files |
|-|-|
| A | `backend/gemini_live/**`, `tests/test_gemini_live_*.py` |
| B | `backend/voice_router/**`, `tests/test_voice_router_*.py` |
| C | `config/voices.yaml`, `backend/voice_text/**` (normalizer), TTS adapter files under `backend/voicekit/providers/` (new files only), `docs/VOICE_EVAL.md`, `tests/test_voice_text_*.py` |
| D | `tests/test_i18n_*.py`, `docs/TRANSLATIONS_REVIEW.md`, proposed i18n patches in `docs/patches/` |
| E | `tools/avatar/**`, `config/avatar.yaml`, `frontend/avatar/assets/*.glb` (new names), `docs/AVATAR.md` |
| F | `tests/**` not listed above (tier selection, handover, guest restriction, voice gender, UI in si/ta, lip sync offset, avatar fallback) |
| Lead (me) | `config.py`, `server.py`, `agent.py`, `app.js`, `index.html`, i18n files, `requirements.txt`, `.env.example`, `INTERFACES.md`, `README.md`, `ARCHITECTURE.md`, `KNOWN_LIMITATIONS.md`, `docs/THIRD_PARTY.md` |

Contracts between A, B and the lead:

```python
# voice_router/types.py (B owns, A and lead import)
class Tier(str, Enum): GEMINI_LIVE="gemini_live"; LIVEKIT="livekit"; LOCAL="local"
@dataclass class HandoverState: language, voice_style, access (AccessContext), summary: str, pending_confirmation: Optional[dict]
def classify(exc_or_code) -> Failure | NotAFailure   # reasons.py
class VoiceRouter: pick(state) -> Tier ; report(tier, outcome) ; status() -> dict ; probe_all()

# gemini_live/session.py (A)
class LiveSessionConfig: api_key, model, language, voice_style, system_instruction, tools, resumption_handle
class GeminiLiveSession:
    async def open(cfg) ; async def send_audio(pcm16_16k: bytes) ; async def close()
    events: AsyncIterator[LiveEvent]   # AudioOut(pcm24k), InputText(text, final), OutputText(text, final),
                                       # Interrupted, TurnComplete, ToolActivity(name), GoAway(seconds), Resumed(handle), Failed(reason_class, detail)
    tool_executor: Callable[[str, dict], Awaitable[dict]]   # injected by the lead; calls mcp_gateway
```

## 9. Risks

1. **No credit on the key**: tier 1 cannot be live tested (section 0). Mitigation: a scripted fake Live server for tests; honest report.
2. Native audio grounding cannot be filtered after the fact (section 3).
3. Sinhala quality of native audio models is unverified; the owner must listen once credit exists.
4. Gemini Live preview models change; the model id is configuration.
5. edge-tts is an unofficial service and broke once (403); it is tier 3's cloud voice, and SAPI/Piper stay behind it.
6. LiveKit Inference Sinhala exists only on Inworld TTS 2.0 (docs); free plan credits were not stated in the docs read; tier 2 Sinhala is therefore marked unverified.
7. Avatar texture edits may break lip sync if shape keys are touched; the pipeline never edits meshes' keys and a test checks them.

## 10. Test plan

Backend: classifier table (every failure type picks the right tier; safety block does not), breaker open/close and upward recovery, handover keeps language, gender, access tier, pending confirmation; guest tool restriction in the Live session (only one declaration; a forged call for another tool is refused by the gateway); fake Live server: resumption on `GoAway`, barge in, transcript events; normalizer table tests per language; voice map gender check per tier. Browser (opt in): full UI in si and ta with no English left, 360 px layouts, avatar load and fallback, lip sync offset (existing method, `docs/ARCHITECTURE.md` "Audio to mouth offset"). Manual: walkthrough per Phase 2.

## 11. ADRs

* **ADR-1** Separate `GEMINI_LIVE_MODEL` (default `gemini-3.8-live`) from `GEMINI_MODEL` (text, default `gemini-flash-lite-latest`). `.env` had a Live model in the text setting, which would make every text call fail with a 404 once credit exists. The local `.env` value is corrected but `.env` is not committed.
* **ADR-2** Backend WebSocket proxy for Gemini Live (section 1).
* **ADR-3** Fall back only on genuine failures (section 4).
* **ADR-4** `gemini-3.8-live` rather than `-extended-thinking`: lower latency; thinking models add delay a phone style conversation does not need.
* **ADR-5** Voices in `config/voices.yaml` only.
