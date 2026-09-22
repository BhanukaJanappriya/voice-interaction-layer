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

import os

from dotenv import load_dotenv

load_dotenv()

# Cloud LLM (multi agent orchestration when reachable)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
CLOUD_LLM_PROVIDER = os.environ.get("CLOUD_LLM_PROVIDER", "gemini").strip().lower()  # "gemini" or "gpt"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Local LLM fallback (always available, no key required)
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "llama3.2:latest")
LOCAL_LLM_HOST = os.environ.get("LOCAL_LLM_HOST", "http://localhost:11434")

# Cloud speech (STT and TTS)
CLOUD_SPEECH_PROVIDER = os.environ.get("CLOUD_SPEECH_PROVIDER", "google").strip().lower()  # "google" or "azure"
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "").strip()

# Local STT fallback
LOCAL_WHISPER_MODEL_SIZE = os.environ.get("LOCAL_WHISPER_MODEL_SIZE", "base")

SUPPORTED_LANGUAGES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}


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
