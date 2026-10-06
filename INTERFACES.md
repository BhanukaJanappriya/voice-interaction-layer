Interface contracts for the voice interaction backend.

These signatures are fixed so backend/vad.py, backend/stt.py,
backend/agent.py, backend/tts.py, and backend/server.py can be read,
tested, and modified independently. Do not change them without updating
this file and checking every caller.

backend/config.py
    Reads all environment configuration once (API keys, provider choices,
    model sizes) from a .env file (see .env.example). Every other module
    imports from here rather than touching os.environ directly.

    def cloud_llm_configured() -> bool
    def cloud_speech_configured() -> bool
        Report whether the currently selected provider has a usable key
        present. Do not make a network call; cannot detect an invalid key
        or an outage, only whether one was ever attempted.

backend/vad.py
    class VoiceActivityDetector
        def __init__(self, aggressiveness: int = 2)
        def calibrate_noise_floor(self, sample_audio: np.ndarray) -> float
            Sets an adaptive energy floor from a short ambient sample
            recorded with nobody speaking. Call once per session.
        def is_speech(self, audio_chunk: np.ndarray) -> bool
            audio_chunk: float32 mono numpy array at SAMPLE_RATE (16000).
            Combines the calibrated floor with WebRTC VAD's per frame
            classification. Never raises on an empty or short chunk.

    def denoise_audio(audio: np.ndarray, noise_sample: Optional[np.ndarray] = None) -> np.ndarray
        Spectral gating noise reduction. Never raises; falls back to
        returning audio unchanged if the underlying library is unavailable
        for any reason (see KNOWN_LIMITATIONS.md for a real example of
        this on Windows).

    SAMPLE_RATE = 16000  # shared by every module that touches raw audio

backend/stt.py
    @dataclass
    class TranscriptEvent:
        text: str
        is_final: bool
        language: str

    def transcribe_stream(
        audio_chunk_iterator: Iterator[np.ndarray],
        language_hint: Optional[str] = None,
        vad: Optional[VoiceActivityDetector] = None,
    ) -> Iterator[TranscriptEvent]
        Consumes short (about CHUNK_SECONDS = 0.5s) float32 mono chunks at
        SAMPLE_RATE. Yields is_final=False partial events as speech
        continues and one is_final=True event once trailing silence (per
        the given, ideally pre-calibrated, VoiceActivityDetector) ends the
        utterance. This is a plain synchronous generator: server.py runs
        it on a background thread and bridges its output back into
        asyncio, rather than this function knowing anything about asyncio
        itself, so it stays trivially unit testable and reusable from the
        standalone microphone test.

backend/agent.py
    class ChatAgent(ABC):
        def respond(self, text_input: str) -> str

    class EchoAgent(ChatAgent)
    class LlamaOllamaAgent(ChatAgent)
        Unchanged in shape from the prototype. LlamaOllamaAgent is the
        local fallback every other path degrades to.

        def respond(self, text_input: str, user_name: Optional[str] = None) -> str
        def respond_stream(self, text_input: str, user_name: Optional[str] = None) -> Iterator[str]
            Like respond(), but yields text chunks as Ollama generates
            them (stream mode) instead of blocking for the full reply.
            Never raises for a request level failure; yields a single
            chunk with the same wording respond() would have returned.
            user_name (None means Guest) goes into the system prompt so
            the reply can address the user by name.
        def warm_up(self) -> None
            Loads the model into Ollama ahead of the first question.

    class GeminiAgent(ChatAgent)
    class GptAgent(ChatAgent)
        def __init__(self, model: str = None, system_prompt: Optional[str] = None)
        def respond_stream(self, text_input: str) -> Iterator[str]
        Raise CloudUnavailableError (never a raw SDK exception) on any
        failure: missing key, network error, auth error, quota error.

    class Orchestrator(ChatAgent)
        def __init__(self, tool_callback: Optional[Callable[[str], Optional[str]]] = None,
                     external_agent_provider: Optional[Callable[[], Optional[ChatAgent]]] = None)
            tool_callback: server.py passes the knowledge base retriever's
            context_for (knowledge/retriever.py). On the cloud path its
            output is the tool/retrieval stage handed to the composer; on
            the local path it is added to the Llama system prompt, one
            model with extra context, not multi agent collaboration.
            external_agent_provider (added): returns the external chat
            agent an admin made active, or None. When it returns one, that
            agent answers first; an ExternalAgentError before any text
            falls back to the normal cloud/local path, after some text a
            short apology is added. A provider that raises counts as none
            active. last_mode is then "external agent (<name>)".
        def respond(self, text_input: str, user_name: Optional[str] = None) -> str
        def respond_stream(self, text_input: str, user_name: Optional[str] = None) -> Iterator[str]
            Streaming counterpart to respond(). Both the cloud and the
            local path stream as they generate. After a cloud failure the
            cloud is skipped for 5 minutes (circuit breaker).
        def warm_up(self) -> None
            Called at server startup: loads the local model and probes
            the cloud provider once.
        self.last_mode: str
            Set after every respond()/respond_stream() call to a human
            readable string describing which path actually served the
            reply (e.g. "cloud multi agent (gemini)" or
            "local single agent (Llama via Ollama)"), for logging and for
            the frontend's mode badge.

    class ExternalAgentError(Exception)
        Every ExternalHttpAgent failure (refused URL, network error, timeout,
        non 2xx, redirect, oversized or unreadable reply, missing response
        path); never a raw requests/urllib3 exception.
    class ExternalHttpAgent(ChatAgent)
        def __init__(self, name, url, method="POST", auth_header_name=None,
                     auth_header_value=None, body_template='{"message": "{text}"}',
                     response_path="reply", timeout_seconds=None, streaming=False)
            timeout_seconds None means config.EXTERNAL_AGENT_DEFAULT_TIMEOUT.
        def respond(self, text_input, user_name=None, language=None) -> str
        def respond_stream(self, text_input, user_name=None, language=None) -> Iterator[str]
            body_template is JSON text; {text}, {user_name}, {language} are
            replaced JSON escaped (placeholders must be inside quotes). GET
            sends the filled template's fields as query parameters.
            response_path is dotted with integer list indexes
            ("choices.0.message.content"). streaming=True reads Server Sent
            Events (data lines, "[DONE]" ends; each event plain text or JSON
            picked by response_path) and falls back to whole JSON when the
            reply is not text/event-stream. URL checked by url_safety before
            every request and pinned at connect time; no redirects; reply
            capped at EXTERNAL_AGENT_MAX_RESPONSE_BYTES while reading.
        self.name: str

backend/url_safety.py
    class UnsafeUrlError(ValueError)
    def check_outbound_url(url, allow_private=None) -> None
        http/https only, no credentials in the URL, every A/AAAA record
        must be public (private, loopback, link local, multicast, reserved,
        unspecified, CGNAT 100.64/10 and IPv4 mapped/6to4/Teredo/NAT64
        equivalents refused). allow_private None means
        config.EXTERNAL_AGENT_ALLOW_PRIVATE; cloud metadata addresses are
        refused even when it is true.
    def safe_session(allow_private=None) -> requests.Session
        Re-checks and pins the resolved address at connect time (DNS
        rebinding), ignores proxy env vars and .netrc.

backend/admin/service.py
    def get_active_agent() -> Optional[ExternalHttpAgent]
        Cached in memory, no database query per turn, never raises.
    def active_agent_summary() -> Optional[dict]      {id, name, usable}, for /health
    def test_agent(agent_id=None, draft=None, message=...) -> {ok, reply, latency_ms, error}
    create_agent / update_agent / delete_agent / activate / deactivate_all / list_agents
        The auth header value is stored encrypted (Fernet,
        FIELD_ENCRYPTION_KEY) and never returned, only has_auth_header.
    Import it by its bare name (admin.service) everywhere: a second import
    path would create a second module with a second cache.
backend/admin/routes.py: router (prefix /admin/api), every route needs an admin session
    GET/POST /agents, PUT/DELETE /agents/{id}, POST /agents/{id}/test,
    POST /agents/test, POST /agents/{id}/activate, POST /agents/deactivate,
    GET/POST /mcp-clients (the secret is returned once), DELETE
    /mcp-clients/{id} (revoke), GET /audit?limit=

backend/knowledge/prompts.py
    GROUNDED_RULES, GENERAL_RULES: the two short prompts (strict, answer only
    from the notes / natural general answers that never invent SLT-Mobitel
    prices, packages, codes or contact details)
    def rules_for(context: Optional[str]) -> str     strict with notes, general without
    def with_context(system_prompt, context) -> str   persona + matching rules (+ notes)

backend/knowledge/retriever.py
    def format_context(results, query=None) -> Optional[str]
        The notes block: at most MAX_CHUNKS (3) chunks, each cut to at most
        MAX_CHUNK_CHARS (700) characters, choosing the sentences that mention
        the most words of query (added, optional), with table noise removed.
    def select_relevant(results, min_score=None) -> list
        Relevance is judged on the plain embedding score; the keyword boost
        (TERM_BOOST) only changes the order.
    def get_retriever() -> KnowledgeRetriever        process wide instance
    class KnowledgeRetriever
        def warm_up(self) -> None
        def search(self, query: str, top_k: int = config.RETRIEVAL_TOP_K) -> list[RetrievedChunk]
            RetrievedChunk: text, score, source_url, retrieved_at, title, metadata
        def context_for(self, text: str) -> Optional[str]
            The Orchestrator's tool_callback. None when the index is
            missing, retrieval is off, Ollama is unreachable or nothing
            scores above RETRIEVAL_MIN_SCORE; never raises. Prints
            "Retrieval latency: N ms".
        def get_package_details(self, name: str) -> list[dict]
        def status(self) -> dict      for /health
    Rebuild: python -m backend.knowledge.build (from the repository root).
    The index holds chunks of about 160 tokens (index.MAX_TOKENS).

backend/db/base.py
    Base, engine, SessionLocal, session_scope() (commits on success),
    get_session() (FastAPI dependency). DATABASE_URL picks the database.
backend/db/models.py
    User, Admin, ExternalAgent, McpClient, AuditLog (one database)
backend/db/migrate.py
    def upgrade_to_head() -> None      Alembic upgrade to head; run at startup; safe when current; raises on failure.
    def database_status() -> dict      {ok, dialect, revision, head}; never raises (for /health).

backend/auth/tokens.py
    def issue_token(subject, audience, ttl_minutes, scopes=None, extra=None) -> tuple[str, int]
    def decode_token(token, audience) -> dict     raises TokenError
    def token_scopes(claims) -> set[str]
    AUDIENCE_USER, AUDIENCE_ADMIN, AUDIENCE_MCP (a token for one is refused as another)
backend/auth/passwords.py
    def hash_secret(plain) -> str / def verify_secret(plain, hashed) -> bool   (bcrypt)
backend/auth/phone.py
    def normalize_sl_landline(raw) -> str      "+94XXXXXXXXX"; raises InvalidPhoneNumber
backend/auth/manage_users.py (command line)
    add / list / unlock / set-pin for customers; admin-password [--username]
    resets an admin's password and clears its lockout

backend/auth/users.py
    @dataclass(frozen=True) class VerifiedUser: id: int; display_name: str; phone_e164: str
    class AuthError(Exception): reason ("invalid" | "locked" | "rate_limited"), retry_after: Optional[int]
    def create_user(phone, display_name, pin, preferred_language=None) -> VerifiedUser
    def verify_user(phone, pin, client_ip) -> VerifiedUser   (blocking; raises AuthError)
backend/auth/admins.py
    def seed_first_admin_from_env() -> bool   creates ADMIN_USERNAME/ADMIN_PASSWORD only when no admin exists
backend/auth/dependencies.py
    def verified_user_from_token(token) -> Optional[VerifiedUser]            never raises; user must still exist
    def verified_user_from_request(conn, explicit_token=None) -> Optional[VerifiedUser]
        conn: Request or WebSocket; tries explicit token, Authorization: Bearer, cookie user_session
    def require_admin(request) -> {"sub": str, "username": str}   FastAPI dependency, 401 otherwise
    def client_ip(conn) -> str
backend/auth/routes.py: router (prefix /auth)
    POST /auth/verify {phone, pin} -> {display_name, token, expires_at} + cookie user_session
        401 invalid (same for unknown number and wrong PIN), 422 bad phone
        format, 423 locked (Retry-After), 429 rate limited (Retry-After)
    POST /auth/logout, GET /auth/me -> {display_name} | 401
    POST /auth/admin/login {username, password} -> {username, expires_at} + cookie admin_session
    POST /auth/admin/logout, GET /auth/admin/me -> {username} | 401

backend/mcp_server/ (MCP server)
    clients.py
        ALLOWED_SCOPES = {"knowledge:read", "assistant:ask"}
        def create_client(name: str, scopes: list[str]) -> tuple[McpClient, str]
            Returns the row and the plain secret, shown once; only a bcrypt
            hash is stored. Raises InvalidClientRequest (a ValueError) for
            an empty name or an unknown scope.
        def list_clients() -> list[dict]      no secrets
        def revoke_client(id: int) -> bool    tokens stop working on their next request
        def authenticate_client(client_id: str, secret: str) -> Optional[McpClient]
        def is_client_active(client_id: str) -> bool
        def record_audit(actor_id, action, outcome, detail=None, duration_ms=None,
                         mcp_client_pk=None, actor_type="mcp_client") -> None   never raises
        def list_audit(limit: int = 100) -> list[dict]
    oauth.py: router with POST /oauth/token (client credentials; Basic or
        form auth; optional scope subset and RFC 8707 resource), GET
        /.well-known/oauth-authorization-server, GET
        /.well-known/oauth-protected-resource[/mcp].
    server.py
        def build_mcp_server(search_fn, package_fn, ask_fn) -> MCPServer
            search_fn(query: str, top_k: int) -> list[dict]  (text, score, source_url, retrieved_at, title)
            package_fn(name: str) -> list[dict]
            ask_fn(question: str, language: Optional[str]) -> tuple[str, str]  (reply, mode)
            Tools: search_knowledge(query, top_k=4) and get_package_details(name)
            need knowledge:read; ask_assistant(question, language=None) needs
            assistant:ask. One audit row per call, never the query or answer text.
    mount.py
        def mount_mcp(app, search_fn, package_fn, ask_fn) -> McpHandle
            Serves /mcp (streamable HTTP, stateless, JSON responses) and the
            OAuth routes. McpHandle.lifespan() is entered in the app
            lifespan (the SDK session manager can run once per process);
            McpHandle.status() -> {enabled, path, running, active_clients}.
        Missing/invalid/expired/revoked token -> 401 with
        WWW-Authenticate: Bearer ... resource_metadata="<PUBLIC_BASE_URL>/.well-known/oauth-protected-resource/mcp".
        Tool without its scope -> 403 with WWW-Authenticate: Bearer
        error="insufficient_scope", scope="<scope>", and a JSON-RPC error
        body (code -32003, data.error "insufficient_scope").

    Behaviour added to transcribe_stream() without changing its signature:
    partial events always use the main local model (config.LOCAL_WHISPER_
    MODEL_SIZE, beam search STT_BEAM_SIZE, hotwords STT_HOTWORDS, Whisper's
    speech filter STT_VAD_FILTER) and no noise filter (STT_DENOISE is off).
    The final event may be rechecked with config.STT_ACCURATE_MODEL when the
    main model's average log probability is below STT_CONFIDENCE_FLOOR. A
    failing cloud speech call is skipped for 5 minutes. Internal: _emit(audio,
    language_hint, final=False), _transcribe_local(audio, language_hint,
    final=False).

backend/tts.py
    def speak_stream(sentences: Iterable[str], language: str = "en",
                     voice_style: str = "female_natural",
                     on_sentence_start: Optional[Callable[[str], None]] = None) -> None
        Speaks sentences as they arrive, even from an iterable that is
        still being filled while the agent generates. Synthesis of the
        next sentence overlaps playback of the current one.
        on_sentence_start is called right before each sentence plays.
    class SentenceChunker
        feed(text) -> list[str] / flush() -> list[str]: turns streamed
        reply text into whole sentences as soon as each one is complete.
    def warm_up() -> None
        Opens the Google TTS connection at startup (no-op otherwise).
    def speak_text(text_input: str, language: str = "en", voice_style: str = "female_natural",
                   on_sentence_start=None) -> None
        Splits text into sentences and speaks them through speak_stream()
        (streamed, pipelined playback). Tries, in order: the configured
        cloud provider if real credentials are set; edge-tts (a free,
        credential free neural voice covering en/si/ta, see this module's
        own docstring); a local SAPI5 voice for English only. voice_style
        "female_natural" selects the female voice at every level; any
        other value (e.g. "male") selects the male voice. Only if every
        level fails does Sinhala/Tamil fall through to a short spoken
        English notice instead of mangling the text; never raises for a
        normal call, only logs.

backend/server.py
    FastAPI app with one WebSocket endpoint, /ws/voice. See the module's
    own docstring for the full message protocol (what the client sends and
    what JSON event types the server sends back). GET /health reports
    which providers are actually configured right now, and (added) the
    database, knowledge, external_agent and mcp status.

    The single app (added): it also includes /auth, /admin/api, /mcp,
    /oauth/token and /.well-known/..., and serves frontend/admin at /admin/
    and frontend at /. Start it with python -m backend (backend/__main__.py).
    Startup runs database migrations and the first admin seed before the
    background warm up.

    /ws/voice's agent_response_chunk event carries the accumulated reply
    text as it streams in from Orchestrator.respond_stream(), followed by
    one final agent_response event with the complete text and mode. Audio
    starts before that, as soon as the first sentence is complete; each
    sentence is announced with a speaking_sentence event as it starts.
    start_session carries user_name and greet; the server speaks the
    introduction first when greet is true. It may also carry session_token
    (added, optional): with it, or with the user_session cookie on the
    handshake, the user is verified and user_name is taken from their
    account instead of the message.

    POST /chat, body {"text": str, "language": str | null, "user_name": str | null}
        -> {"reply": str, "mode": str}
        Unchanged contract. With Authorization: Bearer <user token> or the
        user_session cookie, the verified account's name is used instead
        of user_name.
        Not streamed: a plain request/response endpoint, unlike the
        WebSocket voice path.
        Text only path for the frontend's chat screen. Runs the same
        Orchestrator as the voice pipeline (same cloud/local fallback and
        the same last_mode reporting), with no STT or TTS involved.

Shared conventions
    Every SAMPLE_RATE, CHUNK_SECONDS, and similar constant lives in exactly
    one module (vad.py for SAMPLE_RATE, stt.py for CHUNK_SECONDS) and is
    imported everywhere else, never redefined.
    Each module prints its own latency for its own operation, as in the
    prototype; server.py additionally logs one per turn total.
    Each module is runnable standalone from inside backend/:
    python vad.py / python stt.py / python agent.py / python tts.py.
    New packages keep the bare name imports (import config, from
    db.models import User); backend/__init__.py puts backend/ on sys.path
    so package style entry points (python -m backend...) work from the
    repository root.
    Only config.py reads the environment; every new setting is listed in
    .env.example.


Additions for the LiveKit transport, voicekit, the avatar, the MCP hub client
and one time code login (2026-10-05). All are new modules, optional
parameters or new endpoints; no signature above changed.

backend/server.py (additions)
    /ws/voice start_session accepts voice_style ("female_natural" | "male",
    anything else means "female_natural") and, during a session, a
    {"type": "set_voice", "voice_style": ...} text frame changes it from the
    next sentence. _run_one_utterance, _speak, _speak_canned_reply and
    _speak_streamed_reply gained an optional voice style parameter whose
    default is the old behaviour.
    GET /health also reports mcp_hub, livekit {transport, configured,
    available, worker}, avatar_renderer and voicekit {enabled,
    api_keys_configured}.
    The Orchestrators are built with compose_tool_callbacks(retriever.context_for,
    hub_context_for), retrieval first.

backend/config.py (additions, all in .env.example)
    VOICE_TRANSPORT (livekit | websocket), LIVEKIT_URL / API_KEY / API_SECRET /
    TOKEN_TTL_SECONDS / AGENT_NAME, AVATAR_RENDERER (talkinghead | legacy),
    VOICEKIT_*, MCP_HUB_*, MCP_HUB_IN_REPLIES, MCP_HUB_CONTEXT_BUDGET_SECONDS,
    AUTH_METHODS, OTP_*, OTP_BACKEND, SMTP_*, SMS_*.
    def livekit_configured() -> bool       URL, key and secret all present
    def mcp_hub_configured() -> bool       enabled and an API key present
    Neither makes a network call.

backend/voicekit (standalone: imports only stdlib, numpy and third party libs; see docs/VOICEKIT.md)
    types: AudioChunk(pcm, sample_rate, sentence_index), WordTiming(text, start_ms,
        duration_ms, sentence_index), SentenceStart(text, sentence_index),
        SynthesisDone(provider, first_audio_ms, total_ms), SynthEvent = union of
        those; VoicekitError, ProviderUnavailable, InvalidRequest
    settings: VoicekitSettings(google_credentials_path, azure_key, azure_region,
        cloud_provider="google", local_voice_dir, max_text_chars=2000,
        provider_order=("cloud", "edge", "local_model", "sapi"))
    core: synthesize_stream(text_or_iterable, language="en", voice="female_natural",
          settings=None) -> SynthStream, an Iterator[SynthEvent] with a thread safe
          cancel(). Raises only InvalidRequest. A failing provider falls through; if
          all fail the last event is SynthesisDone(provider="none"). Audio is 16 bit
          mono PCM. WordTiming.start_ms is relative to its sentence.
          list_voices(settings=None) -> [{language, voice, provider, name, license, gender}]
    sinks: Sink.play(events) / Sink.cancel(); SpeakerSink, CallbackSink(fn),
          HttpChunkSink(format).stream(events), WebSocketSink(send_bytes, send_json),
          LiveKitSink(audio_source, sample_rate=24000, loop=None)
    adapter: speak_agent(agent_or_callable, sink=None, language="en",
          voice="female_natural", settings=None) -> Callable[[str], str]
    api: create_router(settings, api_keys: Callable[[], list[str]], rate_limit_per_minute)
          POST /api/tts/stream {text, language, voice, format: pcm|wav|ndjson}; header
            X-Sample-Rate; ndjson lines: sentence | timing | audio (base64) | done;
            word start_ms there is from the start of the utterance
          WS /ws/tts: client speak / cancel; server audio_start, binary PCM, timings,
            done, cancelled, error
          Auth X-API-Key (WS also ?api_key=), constant time compare; 401, 429 (rate
          limit, and 20 failed auths per minute per address), 422, 503. Refuses every
          request while no key is configured.
frontend/voicekit/voicekit.js: window.VoiceKit({baseUrl, apiKey, transport}); speak(text,
    {language, voice}) -> Promise, stop(), onTimings / onStart / onEnd / onError /
    onSentence, analyser, audioContext, audioStartTime, currentTime, audioTimeFor(ms)

backend/livekit_transport/
    router (prefix /api/livekit)
        POST /token {language?, voice_style?, user_name?, session_token?}
            -> {url, token, room, identity, expires_at}; 503 when LiveKit is not configured.
            A verified user (session_token, bearer or cookie) gets identity user-<id> and
            the name from the account; anyone else gets guest-<random>.
        GET /config -> {transport, livekit_configured, avatar_renderer}
    class LiveKitVoiceSession(room, settings: SessionSettings, bindings: Bindings, **injectables)
        async run() -> None   one conversation on a connected rtc room; returns when it ends
        async aclose()
    class Bindings: orchestrator, greeting, looks_like_hallucination, sounds_like_closing_signal,
        sounds_negative, closing_question, closing_goodbye, extract_user_name;
        classmethod from_server(server_module)
    def start_worker(server_module=None) -> WorkerHandle   never raises; handle.stop(), handle.status()
    def build_worker_options() -> livekit.agents.WorkerOptions   raises LiveKitNotConfigured
    errors: TransportError > LiveKitNotConfigured, LiveKitUnavailable, SessionError
    Data topic "va-events" (agent to page): the /ws/voice JSON events plus {"type": "interrupt"}.
    Data topic "va-control" (page to agent): {"type":"set_voice","voice_style"}, {"type":"mute","muted":bool}.
    Audio: the published track "agent-voice", 24000 Hz mono.

frontend/avatar (loaded by avatar/loader.js when GET /api/livekit/config says talkinghead)
    window.avatarBridge: safe at any time (before load, after a fallback, on legacy);
        isActive(), setState(state), setGender("female"|"male"), attachAudioStream(MediaStream),
        speakAudio(source, timings, opts), speakSentence(text), stop()
    window.avatarRenderer: init(container, opts), ready, isActive(), fps(), gender, setState,
        setGender -> Promise<{gender, fallback}>, attachAudioStream, speakAudio -> {addTimings, stop},
        speakSentence (text driven, not audio lip sync), stop, measureSyncOffset(), destroy
    document events: avatar:fallback {reason}, avatar:gender-fallback {requested, shown, reason, message}
frontend/livekit-voice.js: window.LiveKitVoice {isEnabled(baseUrl), fetchConfig(baseUrl), connect(options)}

backend/mcp_client (the MCP hub as a client; the MCP SERVER above is separate)
    errors: HubError > HubUnavailable, HubAuthError, HubToolError, HubBreakerOpen
    client.HubClient: async list_tools(refresh, budget), async call_tool(name, arguments, budget),
        status() -> {configured, enabled, transport, reachable, breaker_open, breaker_seconds_left,
        tools, last_error, tool_selection, customer_lookup_tool}. SSE is tried first and the
        transport is cached; a 401 is never retried; the key is never printed or returned.
    get_hub() -> HubClient (process wide, from config)
    tools.hub_context_for(text) -> Optional[str]      sync, never raises; None unless
        MCP_HUB_IN_REPLIES is true. Only search_knowledgebase_tool by default (MCP_HUB_TOOLS
        replaces that list). Tools starting purchase_, add_, subscribe_, log_ or refresh_, the
        OTP tools and authenticate_with_token are refused even when listed.
    tools.compose_tool_callbacks(*callbacks)         runs them side by side, joins non empty
        outputs in order (retrieval first); a failing callback is left out
    tools.warm_up(), tools.status()                   startup and /health

backend/otp (router prefix /auth)
    GET /auth/methods -> {"methods": ["pin","otp"], "otp_available": bool}
    POST /auth/otp/request {phone, language?} -> {challenge_id, masked_destination, channel,
        expires_in, resend_in}. Unknown numbers get the same shape and nothing is sent.
        422 (reason phone_format), 429 (cooldown or rate_limited, Retry-After), 503, 404 disabled
    POST /auth/otp/verify {challenge_id, code} -> {display_name, token, expires_at} + user_session
        cookie, exactly like /auth/verify. 401 wrong, expired or used (one text), 423 locked
        after 3 attempts, 429, 503
    otp.service: request_otp(phone, client_ip, language=None), verify_otp(challenge_id, code,
        client_ip) -> auth.users.VerifiedUser, OtpError(reason, retry_after), set_contact(phone, mobile, email)
    otp.sender: OtpSender.send(destination, code, language); ConsoleOtpSender (the only place a
        code is printed, development only), EmailOtpSender (SMTP), SmsGatewayOtpSender
    OTP_BACKEND hub | local | auto: hub uses validate_customer_and_send_otp and
        verify_otp_and_authenticate on the MCP hub; local keeps codes hashed here (HMAC, never
        stored or logged in clear)
    db: users.mobile_e164, users.email, table otp_challenges (Alembic revision 0002)
