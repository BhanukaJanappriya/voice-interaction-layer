"""Standalone text to speech module for the voice interaction prototype.

Uses pyttsx3, which drives the Windows SAPI5 engine natively. This was chosen
over heavier offline options (Coqui TTS, Piper) because it needs no model
download and works immediately, offline, on this machine. The tradeoff is
voice naturalness; pyttsx3's SAPI5 voices sound noticeably robotic compared
to neural TTS engines, but for a short prototype timeline, reliability wins.
"""

import time

import pyttsx3


def speak_text(text_input: str) -> None:
    """Synthesizes text_input to speech and plays it on the system default
    audio output device. Blocks until playback finishes."""
    if text_input is None or not text_input.strip():
        return

    try:
        engine = pyttsx3.init()
    except Exception as exc:
        print(f"TTS error: could not initialize the speech engine ({exc}).")
        return

    try:
        start = time.perf_counter()
        engine.say(text_input)
        engine.runAndWait()
        elapsed = time.perf_counter() - start
        print(f"TTS latency (synthesis + playback): {elapsed:.2f}s")
    except Exception as exc:
        print(f"TTS error: playback failed ({exc}). Check that an audio output device is available.")
    finally:
        try:
            engine.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sample_text = "This is a test of the text to speech module."
    print(f"Speaking: {sample_text}")
    speak_text(sample_text)
