"""Example 2: call the voicekit HTTP API from Python with requests.

    set VOICEKIT_URL=http://127.0.0.1:8000
    set VOICEKIT_API_KEY=your-key
    python examples/voicekit/http_client.py

Writes reply.wav. The same request with curl:

    curl -X POST "$VOICEKIT_URL/api/tts/stream" \
      -H "X-API-Key: $VOICEKIT_API_KEY" -H "Content-Type: application/json" \
      -d '{"text": "Hello there.", "language": "en", "voice": "female_natural", "format": "wav"}' \
      -o reply.wav
"""

import base64
import json
import os
import wave

import requests

URL = os.environ.get("VOICEKIT_URL", "http://127.0.0.1:8000")
KEY = os.environ.get("VOICEKIT_API_KEY", "")


def speak_to_wav(text: str, path: str, language: str = "en", voice: str = "female_natural") -> None:
    """ndjson lets us read word timings too; use format "wav" for audio only."""
    response = requests.post(
        f"{URL}/api/tts/stream",
        headers={"X-API-Key": KEY},
        json={"text": text, "language": language, "voice": voice, "format": "ndjson"},
        stream=True,
        timeout=60,
    )
    response.raise_for_status()
    rate = int(response.headers["X-Sample-Rate"])
    pcm = bytearray()
    for line in response.iter_lines():
        if not line:
            continue
        message = json.loads(line)
        if message["type"] == "audio":
            pcm.extend(base64.b64decode(message["pcm"]))
        elif message["type"] == "timing":
            print(f"{message['start_ms']:7.0f} ms  {message['text']}")
        elif message["type"] == "done":
            print(f"provider={message['provider']} first audio {message['first_audio_ms']:.0f} ms")
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(pcm))


if __name__ == "__main__":
    speak_to_wav("Hello there. This came from the voicekit API.", "reply.wav")
    print("wrote reply.wav")
