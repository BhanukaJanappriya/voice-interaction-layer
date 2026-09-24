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
        def __init__(self, tool_callback: Optional[Callable[[str], Optional[str]]] = None)
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
    which providers are actually configured right now.

    /ws/voice's agent_response_chunk event carries the accumulated reply
    text as it streams in from Orchestrator.respond_stream(), followed by
    one final agent_response event with the complete text and mode. Audio
    starts before that, as soon as the first sentence is complete; each
    sentence is announced with a speaking_sentence event as it starts.
    start_session carries user_name and greet; the server speaks the
    introduction first when greet is true.

    POST /chat, body {"text": str, "language": str | null, "user_name": str | null}
        -> {"reply": str, "mode": str}
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
