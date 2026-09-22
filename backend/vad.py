"""Noise robust voice activity detection for the real time voice pipeline.

Replaces the prototype's fixed RMS amplitude threshold with two layers that
work together:

1. WebRTC VAD (the `webrtcvad-wheels` package, a prebuilt Windows wheel of
   Google's WebRTC voice activity classifier): a small, fast, pretrained
   model that looks at the spectral shape of each short audio frame, not
   just its loudness. This makes it far more robust to steady background
   noise (fans, hum, traffic) than a single energy threshold, because
   noise and speech can be equally loud but look different spectrally.
2. An ambient noise calibration step, run once per session before the user
   starts talking, which measures the room's baseline energy and sets an
   adaptive noise floor from it. WebRTC VAD's classification is combined
   with this floor, so quiet speech in a quiet room is not swallowed by a
   floor tuned for a noisy one, and a loud non speech burst is less likely
   to pass in a noisy one.

A third piece, `denoise_audio()`, applies spectral gating noise reduction
(via the `noisereduce` library) to the audio actually sent to transcription.
This is a separate concern from `is_speech()`: VAD decides *when* speech is
happening, denoising cleans up *what* gets recorded, so background noise
baked into a real speech segment does not degrade transcription accuracy.
"""

from typing import Optional

import numpy as np
import webrtcvad

SAMPLE_RATE = 16000
FRAME_MS = 30  # webrtcvad only accepts 10, 20, or 30 ms frames
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)


class VoiceActivityDetector:
    """Stateful VAD: create one instance per session, calibrate it once,
    then call is_speech() per audio chunk."""

    def __init__(self, aggressiveness: int = 2):
        # aggressiveness 0-3, higher values filter out more non speech at
        # the cost of being more willing to clip quiet speech. 2 is a
        # reasonable middle ground for a conversational assistant.
        self._vad = webrtcvad.Vad(aggressiveness)
        self.noise_floor_rms = 0.01  # sane default until calibrate_noise_floor() runs
        self.calibrated = False

    def calibrate_noise_floor(self, sample_audio: np.ndarray) -> float:
        """Measures ambient noise energy from a short sample recorded at
        session start with nobody speaking, and sets the adaptive energy
        floor used alongside WebRTC VAD's per frame decision. Returns the
        floor that was set so the caller can log or display it."""
        if sample_audio is None or sample_audio.size == 0:
            self.calibrated = False
            return self.noise_floor_rms

        rms = float(np.sqrt(np.mean(np.square(sample_audio))))
        # Speech must clear ambient noise by a margin, not just cross zero,
        # otherwise a noisy room re-triggers on its own background level.
        self.noise_floor_rms = max(rms * 2.5, 0.003)
        self.calibrated = True
        return self.noise_floor_rms

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """audio_chunk: float32 mono numpy array sampled at SAMPLE_RATE.
        Internally split into WebRTC VAD's fixed size frames; the chunk
        counts as speech only if both the calibrated energy floor is
        cleared AND a majority of its frames are classified as speech by
        WebRTC VAD, so a loud non speech sound and a quiet steady hum are
        both rejected on their own terms."""
        if audio_chunk is None or audio_chunk.size == 0:
            return False

        rms = float(np.sqrt(np.mean(np.square(audio_chunk))))
        if rms < self.noise_floor_rms:
            return False

        pcm16 = np.clip(audio_chunk * 32768.0, -32768, 32767).astype(np.int16)
        num_frames = len(pcm16) // FRAME_SAMPLES
        if num_frames == 0:
            # Chunk shorter than one VAD frame; the energy gate above is
            # all we can apply.
            return True

        speech_frames = 0
        for i in range(num_frames):
            frame = pcm16[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
            if self._vad.is_speech(frame.tobytes(), SAMPLE_RATE):
                speech_frames += 1

        return speech_frames / num_frames >= 0.5


def denoise_audio(audio: np.ndarray, noise_sample: Optional[np.ndarray] = None) -> np.ndarray:
    """Applies spectral gating noise reduction to captured audio before it
    is sent to transcription. Falls back to returning the audio unchanged
    if noisereduce fails for any reason (e.g. a clip too short to analyze),
    since a transcription attempt on the raw audio is better than none."""
    try:
        import noisereduce as nr

        if noise_sample is not None and noise_sample.size > 0:
            return nr.reduce_noise(y=audio, sr=SAMPLE_RATE, y_noise=noise_sample, stationary=True)
        return nr.reduce_noise(y=audio, sr=SAMPLE_RATE, stationary=True)
    except Exception as exc:
        # Known blocker on this machine: noisereduce depends on scipy, and
        # scipy's compiled linalg extension is refused by a Windows
        # Application Control policy here (ImportError, not a bug in this
        # code). Documented in KNOWN_LIMITATIONS.md. Falling back to raw
        # audio keeps the pipeline working; VAD based speech gating still
        # applies, only the extra spectral cleanup step is skipped.
        print(f"VAD: denoise_audio unavailable ({exc}), using raw audio instead.")
        return audio


def _run_standalone_test():
    """python vad.py: records a short silent calibration sample, then two
    prompted test clips (quiet, then noisy or spoken), and reports how many
    analysis blocks the new VAD flags as speech versus the old prototype's
    fixed threshold, so the improvement can be shown as evidence."""
    import sounddevice as sd

    vad = VoiceActivityDetector(aggressiveness=2)

    print("Calibrating noise floor: stay silent for 1.5 seconds...")
    calib = sd.rec(int(1.5 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    floor = vad.calibrate_noise_floor(calib[:, 0])
    print(f"Noise floor set to RMS {floor:.5f}")

    OLD_FIXED_THRESHOLD = 0.02  # matches the prototype's SPEECH_RMS_THRESHOLD

    def _test_clip(label: str, seconds: float = 4.0):
        input(f"\nPress Enter, then {label} for about {seconds:.0f} seconds...")
        clip = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        audio = clip[:, 0]
        block = FRAME_SAMPLES * 10  # roughly 300ms analysis blocks
        blocks = [audio[i : i + block] for i in range(0, len(audio) - block, block)]
        if not blocks:
            print(f"{label}: clip too short to analyze.")
            return
        new_speech_blocks = sum(1 for b in blocks if vad.is_speech(b))
        old_speech_blocks = sum(
            1 for b in blocks if float(np.sqrt(np.mean(np.square(b)))) >= OLD_FIXED_THRESHOLD
        )
        print(
            f"{label}: {new_speech_blocks}/{len(blocks)} blocks flagged as speech by the "
            f"new calibrated VAD, vs {old_speech_blocks}/{len(blocks)} by the old fixed "
            f"threshold ({OLD_FIXED_THRESHOLD})."
        )

    _test_clip("stay quiet, or make background noise without speaking")
    _test_clip("speak normally")

    print(
        "\nA good result: the quiet/noise clip shows fewer speech blocks under the new "
        "VAD than the old threshold (fewer false triggers), while the speech clip still "
        "registers as speech under both (real speech is not lost)."
    )


if __name__ == "__main__":
    _run_standalone_test()
