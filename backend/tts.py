"""Multilingual, natural voice text to speech for the voice interaction backend.

Primary path: a cloud neural TTS voice (Google Cloud Text to Speech
Neural2, or Azure Neural TTS), which is what makes the "sounds like a
natural human woman, not robotic" requirement genuinely achievable; local
engines (this machine's SAPI5 voices via pyttsx3) are audibly synthetic by
comparison, and neither Google nor Azure Neural voices exist as an offline
option.

Local fallback: pyttsx3 (SAPI5), for English only. This machine has exactly
two installed SAPI5 voices, "David" (male) and "Zira" (female); the female
one is used when voice_style requests a female voice, which gets the local
path as close to the requirement as a synthetic voice can get. Sinhala and
Tamil have no local voice on this machine at all (confirmed by checking the
installed SAPI5 voice list; see KNOWN_LIMITATIONS.md), so with no cloud
credentials configured, speak_text() for those languages plays a short
spoken English notice explaining the gap rather than mangling the text
through an English voice or failing silently, per this project's rule
against silently faking or skipping a blocked capability.

Streaming playback: speak_text() splits the reply into sentences and
synthesizes/plays each one as soon as it is ready, instead of waiting for
the entire response to be synthesized first. This is what actually reduces
perceived latency for a long reply, both locally and against a cloud API.
"""

import re
import time
from typing import Optional

import pyttsx3

import config

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


def _speak_cloud_google(text_input: str, language: str, voice_style: str) -> bool:
    """Synthesizes and plays text_input via Google Cloud Text to Speech
    Neural2 voices. Returns False (never raises) if unavailable, so the
    caller can fall back cleanly."""
    if not config.GOOGLE_APPLICATION_CREDENTIALS:
        return False
    try:
        import io

        import sounddevice as sd
        import soundfile as sf
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        lang_code = {"en": "en-US", "si": "si-LK", "ta": "ta-IN"}.get(language, "en-US")
        ssml_gender = (
            texttospeech.SsmlVoiceGender.FEMALE
            if voice_style == "female_natural"
            else texttospeech.SsmlVoiceGender.MALE
        )
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=lang_code,
            ssml_gender=ssml_gender,
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

        voice_map = {
            ("en", "female_natural"): "en-US-JennyNeural",
            ("en", "male"): "en-US-GuyNeural",
            ("si", "female_natural"): "si-LK-ThiliniNeural",
            ("si", "male"): "si-LK-SameeraNeural",
            ("ta", "female_natural"): "ta-IN-PallaviNeural",
            ("ta", "male"): "ta-IN-ValluvarNeural",
        }
        voice_name = voice_map.get((language, voice_style), "en-US-JennyNeural")

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


def speak_text(text_input: str, language: str = "en", voice_style: str = "female_natural") -> None:
    """Synthesizes text_input to speech and plays it, streaming sentence by
    sentence so playback can start before the whole reply is ready.

    Tries the configured cloud neural TTS provider first for every
    sentence (natural voice, required for Sinhala and Tamil since there is
    no usable local voice for either on this machine). If the cloud
    provider is not configured or a call to it fails, falls back to the
    local SAPI5 engine for English only. For Sinhala or Tamil with no cloud
    path available, speaks a short English notice instead of mangling the
    text through an English voice, and logs the limitation plainly.
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

        if language == "en":
            if _speak_local(sentence, voice_style):
                print(f"TTS latency (local SAPI5): {time.perf_counter() - start:.2f}s")
            else:
                print("TTS error: local playback failed and no cloud provider is available.")
            continue

        # Sinhala or Tamil with no cloud path: documented limitation, not a
        # silent failure. See KNOWN_LIMITATIONS.md.
        notice = (
            f"Natural {config.SUPPORTED_LANGUAGES.get(language, language)} speech requires a "
            "cloud text to speech provider, which is not configured right now."
        )
        print(f"TTS: {notice}")
        _speak_local(notice, voice_style)


def _run_standalone_test():
    """python tts.py: speaks a sample sentence in each supported language,
    so voice quality (and the cloud vs local vs notice fallback behavior)
    can be compared and demoed directly."""
    samples = {
        "en": "This is a test of the natural voice text to speech system.",
        "si": "සිංහල බාෂාවේ පරීක්ෂණයකේ තක්සේත් ජාතිය සඩහශ චේතනය කරණයකින් වේ.",
        "ta": "இது இயற்கை குரலுக்கின் சோதனை ஓரு சோதனை வாக்கியம் ஆகும்.",
    }
    print(f"Cloud speech provider configured: {config.cloud_speech_configured()} ({config.CLOUD_SPEECH_PROVIDER})")
    for language, text in samples.items():
        print(f"\nSpeaking [{config.SUPPORTED_LANGUAGES[language]}]: {text}")
        speak_text(text, language=language, voice_style="female_natural")


if __name__ == "__main__":
    _run_standalone_test()
