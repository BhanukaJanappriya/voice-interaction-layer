"""Central configuration for the voice interaction backend.

All environment dependent settings (API keys, provider choices, model
sizes) are read here once, so every other module imports from config
instead of touching os.environ directly. That keeps credential handling in
one place and makes it obvious which features are active versus falling
back, without digging through every module during a demo.

Values are read from a .env file (see .env.example) via python-dotenv, or
from real environment variables if set. Nothing here requires an API key
to import; the *_configured() helpers just report whether one is present so
callers can decide whether to attempt a cloud path or go straight to the
local fallback.
"""

import base64
import json
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Cloud LLM (multi agent orchestration when reachable)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
CLOUD_LLM_PROVIDER = os.environ.get("CLOUD_LLM_PROVIDER", "gemini").strip().lower()  # "gemini" or "gpt"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
# Extra "reasoning" model call before the reply is composed (see
# agent.py's Orchestrator). Off by default: it has to finish before the
# reply can even start, so it roughly doubles the wait for the first word.
CLOUD_REASONING_STAGE = os.environ.get("CLOUD_REASONING_STAGE", "false").strip().lower() in ("1", "true", "yes")

# Local LLM fallback (always available, no key required)
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "llama3.2:latest")
# 127.0.0.1 rather than localhost: on Windows, "localhost" is tried as
# IPv6 (::1) first, Ollama only listens on IPv4, and every request then
# waited about 2 seconds for that attempt to time out before falling back
# (measured: 2.04s per request with localhost, 0.03s with 127.0.0.1).
LOCAL_LLM_HOST = os.environ.get("LOCAL_LLM_HOST", "http://127.0.0.1:11434")

# Cloud speech (STT and TTS)
CLOUD_SPEECH_PROVIDER = os.environ.get("CLOUD_SPEECH_PROVIDER", "google").strip().lower()  # "google" or "azure"
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "").strip()

# Local STT fallback
LOCAL_WHISPER_MODEL_SIZE = os.environ.get("LOCAL_WHISPER_MODEL_SIZE", "base")
# Words Whisper should expect (faster-whisper hotwords). Empty turns it off.
# The other local transcription settings (STT_DENOISE, STT_BEAM_SIZE,
# STT_ACCURATE_MODEL, STT_CONFIDENCE_FLOOR, STT_VAD_FILTER) are defined
# further down, after
# the helper functions they use.
STT_HOTWORDS = os.environ.get("STT_HOTWORDS", "SLT Mobitel").strip()

SUPPORTED_LANGUAGES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}

# Repository root and the local, gitignored data directory (database,
# knowledge base cache and index, generated secrets). Paths below default
# to it so the app runs from a fresh clone with no extra setup.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "").strip() or PROJECT_ROOT / "data")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _local_secret(name: str, factory) -> str:
    """Returns a secret from the environment, or, when it is not set,
    one generated once and kept in DATA_DIR/secrets.json (gitignored), so
    tokens and encrypted fields survive a restart on a development machine
    without anyone having to invent a key first. A real deployment sets
    the environment variable instead; see .env.example."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    secrets_file = DATA_DIR / "secrets.json"
    stored = {}
    if secrets_file.exists():
        try:
            stored = json.loads(secrets_file.read_text(encoding="utf-8"))
        except ValueError:
            stored = {}
    if not stored.get(name):
        stored[name] = factory()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        secrets_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    return stored[name]


def _new_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


# Server startup: load Whisper, Ollama, TTS and the knowledge index before
# the first request. Tests turn it off so they start in milliseconds.
WARM_UP_ON_STARTUP = _env_bool("WARM_UP_ON_STARTUP", True)
# The address this server is reached at, used in token issuer/audience
# values and in the MCP authorization metadata.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")

# Database: SQLite file by default; any SQLAlchemy URL (for example
# postgresql+psycopg://user:pass@host/db) switches it with no code change.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"

# Signing key for every token this server issues (user sessions, admin
# sessions, MCP access tokens), and the key that encrypts stored external
# agent credentials.
JWT_SECRET = _local_secret("JWT_SECRET", lambda: secrets.token_urlsafe(48))
FIELD_ENCRYPTION_KEY = _local_secret("FIELD_ENCRYPTION_KEY", _new_fernet_key)
# Session cookies are sent over plain http on the local demo; set true
# behind https.
COOKIE_SECURE = _env_bool("COOKIE_SECURE", False)
USER_SESSION_MINUTES = _env_int("USER_SESSION_MINUTES", 60)
ADMIN_SESSION_MINUTES = _env_int("ADMIN_SESSION_MINUTES", 60)
MCP_TOKEN_MINUTES = _env_int("MCP_TOKEN_MINUTES", 15)

# Existing user verification (phone number + PIN)
LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 5)
LOGIN_LOCKOUT_MINUTES = _env_int("LOGIN_LOCKOUT_MINUTES", 15)
# Per client address, across all accounts, to slow down guessing.
LOGIN_RATE_LIMIT_PER_MINUTE = _env_int("LOGIN_RATE_LIMIT_PER_MINUTE", 10)

# First admin account, created at startup only when no admin exists yet.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# External chat agents registered in the admin portal
# Private, loopback and link local addresses are refused (SSRF protection)
# unless this development flag is on.
EXTERNAL_AGENT_ALLOW_PRIVATE = _env_bool("EXTERNAL_AGENT_ALLOW_PRIVATE", False)
EXTERNAL_AGENT_MAX_RESPONSE_BYTES = _env_int("EXTERNAL_AGENT_MAX_RESPONSE_BYTES", 1_000_000)
EXTERNAL_AGENT_DEFAULT_TIMEOUT = _env_float("EXTERNAL_AGENT_DEFAULT_TIMEOUT", 15.0)

# Knowledge base (retrieval augmented generation)
KNOWLEDGE_ENABLED = _env_bool("KNOWLEDGE_ENABLED", True)
KNOWLEDGE_DIR = Path(os.environ.get("KNOWLEDGE_DIR", "").strip() or DATA_DIR / "knowledge")
# Multilingual embedding model served by the same local Ollama as the
# fallback LLM, so Sinhala and Tamil questions find English content.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3").strip()
EMBEDDING_HOST = os.environ.get("EMBEDDING_HOST", "").strip() or LOCAL_LLM_HOST
RETRIEVAL_TOP_K = _env_int("RETRIEVAL_TOP_K", 4)
# Chunks scoring below this cosine similarity are not used as context.
# 0.53 measured (docs/RAG_EVAL.md): small talk tops out at 0.524 with
# bge-m3, real SLT Mobitel questions mostly score 0.55 or more.
RETRIEVAL_MIN_SCORE = _env_float("RETRIEVAL_MIN_SCORE", 0.53)
SCRAPER_USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "SLTDigitalLab-KnowledgeBot/1.0 (voice assistant prototype; contact: SLT Digital Lab)",
).strip()
SCRAPER_DELAY_SECONDS = max(1.0, _env_float("SCRAPER_DELAY_SECONDS", 1.5))
SCRAPER_MAX_PAGES = _env_int("SCRAPER_MAX_PAGES", 400)

# Local speech to text accuracy (see stt.py). Measured on 144 spoken test
# clips (three voices, one with background noise): the noise reduction
# filter that used to run before every transcription made Whisper worse,
# 11.0% word errors against 3.3% without it, so it is off by default.
STT_DENOISE = _env_bool("STT_DENOISE", False)
# Beam search width: how many candidate transcripts Whisper compares.
STT_BEAM_SIZE = _env_int("STT_BEAM_SIZE", 5)
# Whisper's own speech filter, run before decoding. With the brand hotword
# set, Whisper writes words such as "SLT Mobitel" for audio that contains no
# speech (30 of 30 noise clips with the larger model, 7 of 30 with the base
# one); with this filter on, 0 of 30, at no cost in accuracy or speed.
STT_VAD_FILTER = _env_bool("STT_VAD_FILTER", True)
# A larger model for the final transcript of a turn when the main one is
# not confident (empty turns it off). The final transcript decides what is
# asked, so a second look at the uncertain ones is worth its extra second.
STT_ACCURATE_MODEL = os.environ.get("STT_ACCURATE_MODEL", "small").strip()
# Average log probability of a transcript below which the larger model is
# asked to check it (closer to 0 means more confident).
STT_CONFIDENCE_FLOOR = _env_float("STT_CONFIDENCE_FLOOR", -0.40)

# MCP server
MCP_ENABLED = _env_bool("MCP_ENABLED", True)


def database_is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")


def cloud_llm_configured() -> bool:
    """True if the configured cloud LLM provider has a usable API key.
    Does not make a network call, so it cannot detect an invalid key or an
    outage; that is handled at call time by the agent's own fallback logic.
    """
    if CLOUD_LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    if CLOUD_LLM_PROVIDER == "gpt":
        return bool(OPENAI_API_KEY)
    return False


def cloud_speech_configured() -> bool:
    """True if the configured cloud speech provider has usable credentials."""
    if CLOUD_SPEECH_PROVIDER == "google":
        return bool(GOOGLE_APPLICATION_CREDENTIALS)
    if CLOUD_SPEECH_PROVIDER == "azure":
        return bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)
    return False
