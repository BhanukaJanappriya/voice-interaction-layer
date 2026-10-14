"""The voice map, identical to the one in backend/tts.py.

Kept as a copy on purpose (voicekit may not import the host project); a test
compares the two so they cannot drift apart silently.
"""

LANGUAGES = ("en", "si", "ta")
VOICE_STYLES = ("female_natural", "male")

EDGE_VOICES = {
    ("en", "female_natural"): "en-US-AvaMultilingualNeural",
    ("en", "male"): "en-US-AndrewMultilingualNeural",
    ("si", "female_natural"): "si-LK-ThiliniNeural",
    ("si", "male"): "si-LK-SameeraNeural",
    ("ta", "female_natural"): "ta-LK-SaranyaNeural",
    ("ta", "male"): "ta-LK-KumarNeural",
}

# Google Cloud Text to Speech has no Sinhala voice at all (checked live when
# tts.py was written), so there is deliberately no si entry here.
GOOGLE_VOICES = {
    ("en", "female_natural"): ("en-US", "en-US-Chirp3-HD-Kore"),
    ("en", "male"): ("en-US", "en-US-Chirp3-HD-Puck"),
    ("ta", "female_natural"): ("ta-IN", "ta-IN-Chirp3-HD-Kore"),
    ("ta", "male"): ("ta-IN", "ta-IN-Chirp3-HD-Puck"),
}

GENDER = {"female_natural": "female", "male": "male"}


def edge_voice(language: str, voice: str) -> str:
    return EDGE_VOICES.get((language, voice)) or EDGE_VOICES[(language, "female_natural")]


def google_voice(language: str, voice: str):
    return GOOGLE_VOICES.get((language, voice)) or GOOGLE_VOICES.get((language, "female_natural"))
