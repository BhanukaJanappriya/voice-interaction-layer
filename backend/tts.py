"""Multilingual, natural voice text to speech for the voice interaction backend.

Fallback order, from best to last resort:

    1. The configured cloud provider (Google Cloud TTS Neural2, or Azure
       Neural TTS), when real credentials are set. Highest quality, but
       needs an account, credentials, and is subject to that provider's
       free tier quota.
    2. edge-tts: an unofficial but very widely used library that calls
       Microsoft Edge's own text to speech backend (the same neural voice
       catalog Azure Cognitive Services uses) with no API key, account, or
       credentials at all, only internet access. This is what actually
       gets this project a natural sounding, non robotic voice, including
       a real male voice option, without anyone needing to set anything
       up; see edge_tts_voices() below for the specific voices used. It
       needs internet access, so it is not a true offline/local option,
       but it needs nothing to configure, unlike the cloud providers above.
    3. pyttsx3 (SAPI5), fully offline, for English only. This machine has
       exactly two installed SAPI5 voices, "David" (male) and "Zira"
       (female); genuinely the last resort for voice quality, but the only
       option with zero network dependency.
    4. For Sinhala or Tamil, if even edge-tts fails (no internet, or the
       service is unreachable), a short spoken English notice instead of
       mangling the text through an English voice or failing silently, per
       this project's rule against silently faking or skipping a blocked
       capability. This is now the genuinely rare case, not the common
       one: edge-tts alone gets Sinhala and Tamil a real, natural voice, no
       credentials required (verified live, see KNOWN_LIMITATIONS.md).

Streaming playback: speak_text() splits the reply into sentences and
synthesizes/plays each one as soon as it is ready, instead of waiting for
the entire response to be synthesized first. This is what actually reduces
perceived latency for a long reply, at every one of the levels above.
"""

import re
import sys
import time
from typing import Optional

import pyttsx3

import config

# edge-tts and Azure Cognitive Services draw from the same neural voice
# catalog (edge-tts calls Microsoft Edge's own backend for the same
# voices), so this one map serves both _speak_edge_tts() and
# _speak_cloud_azure() below rather than keeping two copies in sync.
_NEURAL_VOICE_MAP = {
    ("en", "female_natural"): "en-US-JennyNeural",
    ("en", "male"): "en-US-GuyNeural",
    ("si", "female_natural"): "si-LK-ThiliniNeural",
    ("si", "male"): "si-LK-SameeraNeural",
    ("ta", "female_natural"): "ta-IN-PallaviNeural",
    ("ta", "male"): "ta-IN-ValluvarNeural",
}

_LOCAL_FEMALE_VOICE_ID = None
_LOCAL_MALE_VOICE_ID = None
_voices_scanned = False


def _scan_local_voices():
    """Finds this machine's installed SAPI5 voice IDs once, so later calls
    do not re-enumerate voices on every utterance."""
    global _LOCAL_FEMALE_VOICE_ID, _LOCAL_MALE_VOICE_ID, _voices_scanned
    if _voices_scanned:
        return
    try:
        engine = pyttsx3.init()
        for voice in engine.getProperty("voices"):
            name = (voice.name or "").lower()
            gender = str(getattr(voice, "gender", "") or "").lower()
            if "female" in gender or "zira" in name:
                _LOCAL_FEMALE_VOICE_ID = voice.id
            elif "male" in gender or "david" in name:
                _LOCAL_MALE_VOICE_ID = voice.id
        engine.stop()
    except Exception as exc:
        print(f"TTS: could not enumerate local voices ({exc}).")
    _voices_scanned = True


def _split_sentences(text: str) -> list[str]:
    """Splits text into sentence sized chunks for streamed playback. A
    simple punctuation split is good enough here; this is not meant to be
    a linguistically precise sentence tokenizer, just a way to start
    speaking before the whole reply is ready."""
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _speak_local(text_input: str, voice_style: str) -> bool:
    """Speaks text_input using the local SAPI5 engine. Returns True if
    playback happened, False if the local engine could not be used."""
    _scan_local_voices()
    try:
        engine = pyttsx3.init()
        voice_id = _LOCAL_FEMALE_VOICE_ID if voice_style == "female_natural" else _LOCAL_MALE_VOICE_ID
        if voice_id:
            engine.setProperty("voice", voice_id)
        engine.say(text_input)
        engine.runAndWait()
        engine.stop()
        return True
    except Exception as exc:
        print(f"TTS error: local playback failed ({exc}).")
        return False


# Explicit Chirp3-HD voice names rather than letting Google auto pick any
# voice that matches a requested gender: gender only selection can land on
# any tier in Google's catalog, including the much older, audibly synthetic
# "Standard" voices, not necessarily the natural sounding ones. Chirp3-HD
# is Google's newest, most natural voice generation (confirmed against the
# real account's own voice list, not assumed); the same character names
# exist across its supported languages, so one small map covers both.
# Sinhala has no entry at all: confirmed live against the real API that
# Google Cloud Text to Speech has zero si-LK voices in its catalog, not a
# transient error, so _speak_cloud_google() skips the network call for
# Sinhala entirely rather than making a request that can only fail. See
# KNOWN_LIMITATIONS.md.
_GOOGLE_CHIRP_VOICE_MAP = {
    ("en", "female_natural"): ("en-US", "en-US-Chirp3-HD-Kore"),
    ("en", "male"): ("en-US", "en-US-Chirp3-HD-Puck"),
    ("ta", "female_natural"): ("ta-IN", "ta-IN-Chirp3-HD-Kore"),
    ("ta", "male"): ("ta-IN", "ta-IN-Chirp3-HD-Puck"),
}


def _speak_cloud_google(text_input: str, language: str, voice_style: str) -> bool:
    """Synthesizes and plays text_input via Google Cloud Text to Speech's
    Chirp3-HD voices. Returns False (never raises) if unavailable, so the
    caller can fall back cleanly."""
    if not config.GOOGLE_APPLICATION_CREDENTIALS:
        return False
    voice_entry = _GOOGLE_CHIRP_VOICE_MAP.get((language, voice_style)) or _GOOGLE_CHIRP_VOICE_MAP.get(
        (language, "female_natural")
    )
    if voice_entry is None:
        return False
    lang_code, voice_name = voice_entry
    try:
        import io

        import sounddevice as sd
        import soundfile as sf
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=lang_code,
            name=voice_name,
        )
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16)
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text_input),
            voice=voice_params,
            audio_config=audio_config,
        )
        data, samplerate = sf.read(io.BytesIO(response.audio_content), dtype="float32")
        sd.play(data, samplerate)
        sd.wait()
        return True
    except Exception as exc:
        print(f"TTS: Google Cloud TTS unavailable ({exc}), falling back.")
        return False


def _speak_cloud_azure(text_input: str, language: str, voice_style: str) -> bool:
    """Synthesizes and plays text_input via Azure Neural TTS. Returns False
    (never raises) if unavailable, so the caller can fall back cleanly."""
    if not (config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION):
        return False
    try:
        import azure.cognitiveservices.speech as speechsdk

        voice_name = _NEURAL_VOICE_MAP.get((language, voice_style), "en-US-JennyNeural")

        speech_config = speechsdk.SpeechConfig(
            subscription=config.AZURE_SPEECH_KEY, region=config.AZURE_SPEECH_REGION
        )
        speech_config.speech_synthesis_voice_name = voice_name
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config)
        result = synthesizer.speak_text_async(text_input).get()
        return result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted
    except Exception as exc:
        print(f"TTS: Azure Neural TTS unavailable ({exc}), falling back.")
        return False


def _speak_cloud(text_input: str, language: str, voice_style: str) -> bool:
    if config.CLOUD_SPEECH_PROVIDER == "azure":
        return _speak_cloud_azure(text_input, language, voice_style)
    return _speak_cloud_google(text_input, language, voice_style)


def _speak_edge_tts(text_input: str, language: str, voice_style: str) -> bool:
    """Synthesizes and plays text_input via edge-tts (see this module's
    docstring for what it is and why it sits here in the fallback order).
    Returns False (never raises) on any failure, most commonly no internet
    access, so the caller falls back to the fully offline local voice.

    edge-tts's own API is async only (it streams audio over a websocket to
    Microsoft's service); asyncio.run() drives it synchronously here since
    the rest of this module, and speak_text()'s sentence by sentence
    playback loop, are synchronous by design. soundfile can decode the
    MP3 bytes edge-tts returns directly (confirmed against a real sample;
    no separate MP3 decoding library needed), the same way the cloud
    provider functions above decode their own audio bytes."""
    voice_name = (
        _NEURAL_VOICE_MAP.get((language, voice_style))
        or _NEURAL_VOICE_MAP.get((language, "female_natural"))
        or "en-US-JennyNeural"
    )
    try:
        import asyncio
        import io

        import edge_tts
        import sounddevice as sd
        import soundfile as sf

        async def _synthesize() -> bytes:
            communicate = edge_tts.Communicate(text_input, voice_name)
            audio_bytes = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
            return bytes(audio_bytes)

        audio_bytes = asyncio.run(_synthesize())
        if not audio_bytes:
            return False
        data, samplerate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        sd.play(data, samplerate)
        sd.wait()
        return True
    except Exception as exc:
        print(f"TTS: edge-tts unavailable ({exc}), falling back.")
        return False


def speak_text(text_input: str, language: str = "en", voice_style: str = "female_natural") -> None:
    """Synthesizes text_input to speech and plays it, streaming sentence by
    sentence so playback can start before the whole reply is ready.

    Tries, in order: the configured cloud provider (if real credentials are
    set), then edge-tts (natural, needs no credentials, only internet; see
    this module's docstring), then the local SAPI5 engine for English
    only. For Sinhala or Tamil, if edge-tts also fails (no internet), a
    short spoken English notice instead of mangling the text through an
    English voice, and logs the limitation plainly.
    """
    if text_input is None or not text_input.strip():
        return

    sentences = _split_sentences(text_input) or [text_input]
    cloud_available = config.cloud_speech_configured()

    for sentence in sentences:
        start = time.perf_counter()

        if cloud_available and _speak_cloud(sentence, language, voice_style):
            print(f"TTS latency (cloud, {language}): {time.perf_counter() - start:.2f}s")
            continue

        if _speak_edge_tts(sentence, language, voice_style):
            print(f"TTS latency (edge-tts, {language}): {time.perf_counter() - start:.2f}s")
            continue

        if language == "en":
            if _speak_local(sentence, voice_style):
                print(f"TTS latency (local SAPI5): {time.perf_counter() - start:.2f}s")
            else:
                print("TTS error: local playback failed and no cloud or edge-tts path is available.")
            continue

        # Sinhala or Tamil with no cloud path and no internet for edge-tts
        # either: documented limitation, not a silent failure. See
        # KNOWN_LIMITATIONS.md. Genuinely rare now that edge-tts covers
        # both languages with no credentials required.
        notice = (
            f"Natural {config.SUPPORTED_LANGUAGES.get(language, language)} speech needs "
            "either a cloud provider or internet access for the free voice service, "
            "and neither is available right now."
        )
        print(f"TTS: {notice}")
        _speak_local(notice, voice_style)


def _run_standalone_test():
    """python tts.py: speaks a sample sentence in each supported language
    with the default female voice, then the same English sentence again
    with the male voice, so voice quality (and the cloud vs edge-tts vs
    local vs notice fallback behavior) can be compared and demoed
    directly, including hearing the male option this test specifically
    exercises."""
    # Windows consoles often default to a legacy codepage (cp1252) that
    # cannot encode Sinhala or Tamil script at all; printing the sample
    # text below would crash the whole test before it ever got to
    # speaking anything. Reconfiguring stdout to UTF-8 here, with
    # replacement for any character even UTF-8 output cannot render in
    # that terminal, keeps this test runnable from a plain terminal
    # rather than requiring the operator to change their system codepage
    # first. errors="replace" rather than "strict" because a print
    # statement failing is not worth treating as fatal here.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # older Python or a stream that does not support reconfigure(); best effort only

    samples = {
        "en": "This is a test of the natural voice text to speech system.",
        "si": "සිංහල බාෂාවේ පරීක්ෂණයකේ තක්සේත් ජාතිය සඩහශ චේතනය කරණයකින් වේ.",
        "ta": "இது இயற்கை குரலுக்கின் சோதனை ஓரு சோதனை வாக்கியம் ஆகும்.",
    }
    print(f"Cloud speech provider configured: {config.cloud_speech_configured()} ({config.CLOUD_SPEECH_PROVIDER})")
    for language, text in samples.items():
        print(f"\nSpeaking [{config.SUPPORTED_LANGUAGES[language]}, female]: {text}")
        speak_text(text, language=language, voice_style="female_natural")

    print("\nSpeaking [English, male]: This is the male voice option.")
    speak_text("This is the male voice option.", language="en", voice_style="male")


if __name__ == "__main__":
    _run_standalone_test()
