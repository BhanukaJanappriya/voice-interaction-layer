"""Configuration for voicekit, passed in as a plain object.

voicekit never reads environment variables or the host project's config
module, so it can be embedded anywhere. The host builds one VoicekitSettings
from its own configuration (see docs/VOICEKIT.md for the mapping).
"""

from dataclasses import dataclass


@dataclass
class VoicekitSettings:
    # Path to a Google service account JSON file; empty disables Google.
    google_credentials_path: str = ""
    azure_key: str = ""
    azure_region: str = ""
    # Which cloud provider the "cloud" slot means: "google" or "azure".
    cloud_provider: str = "google"
    # Folder with local Piper models (see providers/local_model.py); empty
    # or missing disables the local model provider.
    local_voice_dir: str = ""
    max_text_chars: int = 2000
    # Slots tried in this order. "cloud" is google or azure, "edge" is
    # edge-tts, "local_model" is Piper through onnxruntime, "sapi" is the
    # Windows speech engine through pyttsx3.
    provider_order: tuple = ("cloud", "edge", "local_model", "sapi")
