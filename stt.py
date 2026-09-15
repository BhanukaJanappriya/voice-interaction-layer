"""Speech to text module for the voice interaction prototype.

Records audio from the default microphone and transcribes it with
faster-whisper. Standalone and safe to import from other modules.
"""

import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000  # faster-whisper expects 16 kHz mono audio
CHANNELS = 1
BLOCK_SECONDS = 0.5  # size of each recording chunk used for silence checks
SILENCE_RMS_THRESHOLD = 0.01  # below this RMS amplitude a block counts as silence
SPEECH_RMS_THRESHOLD = 0.02  # above this RMS amplitude a block counts as speech

# Model weights are downloaded from Hugging Face the first time this runs
# and are cached locally after that, so later runs work fully offline.
# The model is loaded once and reused across calls (module level singleton).
_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


def _record_audio(duration: float, silence_timeout: float | None) -> np.ndarray | None:
    """Records from the default microphone and returns a float32 mono array,
    or None if the microphone could not be used."""
    block_frames = int(BLOCK_SECONDS * SAMPLE_RATE)
    num_blocks = max(1, int(round(duration / BLOCK_SECONDS)))

    blocks = []
    speech_detected = False
    silence_run = 0.0

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=block_frames,
        ) as stream:
            for _ in range(num_blocks):
                block, _overflow = stream.read(block_frames)
                block = block[:, 0]
                blocks.append(block)

                rms = float(np.sqrt(np.mean(np.square(block))))
                if rms >= SPEECH_RMS_THRESHOLD:
                    speech_detected = True
                    silence_run = 0.0
                elif rms < SILENCE_RMS_THRESHOLD:
                    silence_run += BLOCK_SECONDS
                else:
                    silence_run = 0.0

                if silence_timeout is not None and speech_detected and silence_run >= silence_timeout:
                    break
    except PermissionError:
        print("STT error: microphone access was denied by the OS. Check microphone privacy settings.")
        return None
    except sd.PortAudioError as exc:
        print(f"STT error: no usable microphone device found ({exc}).")
        return None
    except OSError as exc:
        print(f"STT error: could not open the microphone ({exc}).")
        return None

    if not blocks:
        return None
    return np.concatenate(blocks)


def transcribe_audio(duration: float = 5.0, silence_timeout: float | None = None) -> str:
    """Records from the default microphone and returns transcribed text.
    Returns "" (empty string) if no speech was detected or audio was unclear.
    Must never raise for a normal silent/empty recording."""
    audio = _record_audio(duration, silence_timeout)
    if audio is None or audio.size == 0:
        print("STT: no audio captured.")
        return ""

    overall_rms = float(np.sqrt(np.mean(np.square(audio))))
    if overall_rms < SILENCE_RMS_THRESHOLD:
        print("STT: recording was silent, skipping transcription.")
        return ""

    try:
        model = _get_model()
    except Exception as exc:
        print(f"STT error: failed to load the whisper model ({exc}).")
        return ""

    try:
        start = time.time()
        segments, _info = model.transcribe(audio, language="en")
        text = "".join(segment.text for segment in segments).strip()
        latency = time.time() - start
        print(f"STT latency: {latency:.2f}s")
    except Exception as exc:
        print(f"STT error: transcription failed ({exc}).")
        return ""

    if not text:
        print("STT: transcription produced no text.")
        return ""

    return text


if __name__ == "__main__":
    print("Recording for about 5 seconds, speak now...")
    result = transcribe_audio(duration=5.0, silence_timeout=2.0)
    if result:
        print(f"Transcribed text: {result}")
    else:
        print("Transcribed text: (empty)")
