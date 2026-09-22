"""Streaming, multilingual speech to text for the real time voice pipeline.

Replaces the prototype's record-then-transcribe call with
transcribe_stream(), which consumes short audio chunks as they arrive (as
they would over a live WebSocket connection) and yields partial and final
transcript events, using VoiceActivityDetector (vad.py) to decide when
speech starts, when to refresh a partial guess, and when enough trailing
silence means the utterance is finished.

Honest engineering note on "streaming": faster-whisper (the local engine)
has no native partial decoding API; it transcribes whatever audio buffer
you give it, start to finish, each time you call it. So the "partial"
events here are produced by re-running transcription on the growing speech
buffer every few chunks, not by a model that natively emits words as it
hears them. This still cuts perceived latency a lot versus record-8-seconds
-then-transcribe (the prototype's behavior), because the user sees text
appear while they are still talking, but it is not the same as a true
streaming ASR API (which Google Cloud Speech to Text's streaming mode, for
example, actually provides). This tradeoff is documented in
KNOWN_LIMITATIONS.md rather than glossed over.

Language handling: English can always run locally via faster-whisper.
Sinhala and Tamil are attempted through the configured cloud speech
provider first (Google Cloud Speech to Text has explicit si-LK and ta-IN
support); if that is not configured or fails, they fall back to the same
local Whisper model, whose multilingual training does cover Sinhala and
Tamil but with materially lower accuracy than English, again documented
rather than hidden.
"""

import time
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np
from faster_whisper import WhisperModel

import config
from vad import SAMPLE_RATE, VoiceActivityDetector, denoise_audio

_model: Optional[WhisperModel] = None
_model_size: Optional[str] = None


def _get_local_model(model_size: str) -> WhisperModel:
    global _model, _model_size
    if _model is None or _model_size != model_size:
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _model_size = model_size
    return _model


@dataclass
class TranscriptEvent:
    text: str
    is_final: bool
    language: str


def _transcribe_local(audio: np.ndarray, language_hint: Optional[str]) -> tuple[str, str]:
    model = _get_local_model(config.LOCAL_WHISPER_MODEL_SIZE)
    segments, info = model.transcribe(audio, language=language_hint)
    text = "".join(segment.text for segment in segments).strip()
    return text, (language_hint or info.language)


def _transcribe_cloud_google(audio: np.ndarray, language_hint: str) -> Optional[tuple[str, str]]:
    try:
        from google.cloud import speech

        client = speech.SpeechClient()
        lang_code = {"si": "si-LK", "ta": "ta-IN", "en": "en-US"}.get(language_hint, "en-US")
        audio_int16 = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16).tobytes()
        recognition_audio = speech.RecognitionAudio(content=audio_int16)
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE,
            language_code=lang_code,
        )
        response = client.recognize(config=recognition_config, audio=recognition_audio)
        text = " ".join(
            result.alternatives[0].transcript for result in response.results if result.alternatives
        ).strip()
        return (text, language_hint) if text else None
    except Exception as exc:
        print(f"STT cloud error (google): {exc}; falling back to local Whisper.")
        return None


def _transcribe_cloud(audio: np.ndarray, language_hint: str) -> Optional[tuple[str, str]]:
    """Attempts cloud speech recognition for the given language. Returns
    None (never raises) if cloud speech is not configured or the call
    fails, so the caller falls back to local Whisper without special
    casing at the call site."""
    if not config.cloud_speech_configured():
        return None
    if config.CLOUD_SPEECH_PROVIDER == "google":
        return _transcribe_cloud_google(audio, language_hint)
    # Azure Speech to Text is documented as a configured option but not
    # implemented for STT in this pass; see KNOWN_LIMITATIONS.md.
    return None


def _emit(audio: np.ndarray, language_hint: Optional[str]) -> tuple[str, str]:
    start = time.time()
    audio = denoise_audio(audio)

    if language_hint in ("si", "ta"):
        cloud_result = _transcribe_cloud(audio, language_hint)
        if cloud_result is not None:
            text, lang = cloud_result
            print(f"STT latency ({language_hint}, cloud): {time.time() - start:.2f}s")
            return text, lang
        print(
            f"STT: cloud speech not available for '{language_hint}', "
            "using local Whisper instead (lower accuracy expected for this language)."
        )

    text, lang = _transcribe_local(audio, language_hint)
    print(f"STT latency ({language_hint or 'auto'}, local): {time.time() - start:.2f}s")
    return text, lang


# Tuned for ~0.5s audio chunks, matching the chunk size main.py/server.py
# use when reading from the microphone or WebSocket.
CHUNK_SECONDS = 0.5
SILENCE_CHUNKS_TO_FINALIZE = 3  # ~1.5s of trailing silence ends an utterance
REFRESH_CHUNKS = 4  # emit a partial roughly every 2s of new speech


def transcribe_stream(
    audio_chunk_iterator: Iterator[np.ndarray],
    language_hint: Optional[str] = None,
    vad: Optional[VoiceActivityDetector] = None,
) -> Iterator[TranscriptEvent]:
    """Consumes an iterator of short float32 mono audio chunks (about
    CHUNK_SECONDS each, at SAMPLE_RATE) and yields TranscriptEvent objects:
    is_final=False partials as speech continues, then one is_final=True
    event once trailing silence (per the VoiceActivityDetector) indicates
    the utterance is complete.

    Pass an already calibrated VoiceActivityDetector (see vad.py) so the
    session's noise floor is respected; if none is given, a fresh one with
    default settings is created, which is fine for the standalone test
    below but not for a real session that should calibrate first.
    """
    vad = vad or VoiceActivityDetector()
    buffer: list[np.ndarray] = []
    trailing_silence_chunks = 0
    chunks_since_partial = 0
    speech_started = False

    for chunk in audio_chunk_iterator:
        is_speech = vad.is_speech(chunk)

        if is_speech:
            buffer.append(chunk)
            trailing_silence_chunks = 0
            speech_started = True
            chunks_since_partial += 1
        elif speech_started:
            buffer.append(chunk)  # keep a little trailing context for the final pass
            trailing_silence_chunks += 1

        if not speech_started:
            continue

        if chunks_since_partial >= REFRESH_CHUNKS:
            text, lang = _emit(np.concatenate(buffer), language_hint)
            if text:
                yield TranscriptEvent(text=text, is_final=False, language=lang)
            chunks_since_partial = 0

        if trailing_silence_chunks >= SILENCE_CHUNKS_TO_FINALIZE:
            text, lang = _emit(np.concatenate(buffer), language_hint)
            yield TranscriptEvent(text=text, is_final=True, language=lang)
            buffer = []
            speech_started = False
            trailing_silence_chunks = 0
            chunks_since_partial = 0

    # Stream ended (e.g. WebSocket closed) while mid utterance: flush what
    # was captured as a final event rather than dropping it silently.
    if speech_started and buffer:
        text, lang = _emit(np.concatenate(buffer), language_hint)
        if text:
            yield TranscriptEvent(text=text, is_final=True, language=lang)


def _microphone_chunk_iterator(seconds: float) -> Iterator[np.ndarray]:
    import sounddevice as sd

    block_frames = int(CHUNK_SECONDS * SAMPLE_RATE)
    num_blocks = max(1, int(round(seconds / CHUNK_SECONDS)))
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block_frames) as stream:
        for _ in range(num_blocks):
            block, _overflow = stream.read(block_frames)
            yield block[:, 0]


def _run_standalone_test():
    """python stt.py: lets you pick a language, calibrates the noise floor
    briefly, then streams microphone audio through transcribe_stream() and
    prints partial and final events as they arrive, so streaming
    transcription can be verified in each supported language on its own."""
    import sounddevice as sd

    print("Supported languages:", ", ".join(f"{code} ({name})" for code, name in config.SUPPORTED_LANGUAGES.items()))
    language_hint = input("Language code to test (en/si/ta, blank for auto): ").strip() or None

    vad = VoiceActivityDetector()
    print("Calibrating noise floor: stay silent for 1.5 seconds...")
    calib = sd.rec(int(1.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    vad.calibrate_noise_floor(calib[:, 0])

    print("Speak for up to 10 seconds. A pause of about 1.5s ends the utterance.\n")
    for event in transcribe_stream(_microphone_chunk_iterator(10.0), language_hint=language_hint, vad=vad):
        label = "FINAL" if event.is_final else "partial"
        print(f"[{label}] ({event.language}) {event.text}")
        if event.is_final:
            break


if __name__ == "__main__":
    _run_standalone_test()
