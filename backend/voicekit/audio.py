"""Small PCM helpers: resampling, WAV headers, MP3 decoding."""

import io
import struct

import numpy as np


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear interpolation resampler for 16 bit mono PCM. Plain numpy, so it
    needs no compiled audio library. Good enough for speech (it is only used
    when a fallback provider speaks at another rate than the first one)."""
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype("float32")
    n_out = max(1, int(round(len(samples) * dst_rate / src_rate)))
    positions = np.linspace(0, len(samples) - 1, n_out)
    out = np.interp(positions, np.arange(len(samples)), samples)
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()


def wav_header(sample_rate: int, data_len: int = 0xFFFFFFFF) -> bytes:
    """A 44 byte WAV header for 16 bit mono PCM. The default length marks a
    stream of unknown size (players read until the connection closes)."""
    riff_len = 0xFFFFFFFF if data_len == 0xFFFFFFFF else 36 + data_len
    return (
        b"RIFF" + struct.pack("<I", riff_len) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data" + struct.pack("<I", data_len)
    )


class Mp3StreamDecoder:
    """Decodes an MP3 byte stream that arrives in pieces.

    soundfile (libsndfile) cannot decode a partial MP3 incrementally, so each
    call re-decodes everything received so far and returns only the samples
    not returned before. That is quadratic, but a spoken sentence is a few
    hundred KB at most at this bit rate, and decoding takes milliseconds. The
    decoder is deterministic for a given prefix, so the part already returned
    never changes. A trailing partial frame is simply not decoded yet."""

    # Do not bother decoding until this much new data arrived (about 0.35 s
    # of audio at the 48 kbit/s edge-tts uses), to keep the work bounded.
    MIN_NEW_BYTES = 2000

    def __init__(self):
        self._data = bytearray()
        self._decoded_at = 0
        self._emitted = 0
        self.sample_rate = 0

    @property
    def has_data(self) -> bool:
        return bool(self._data)

    def _decode_all(self):
        import soundfile as sf

        samples, rate = sf.read(io.BytesIO(bytes(self._data)), dtype="int16", always_2d=False)
        if samples.ndim > 1:
            samples = samples.mean(axis=1).astype("int16")
        return samples, rate

    def feed(self, data: bytes, final: bool = False):
        """Returns new (pcm_bytes, sample_rate), or None when nothing new."""
        self._data.extend(data)
        if not final and len(self._data) - self._decoded_at < self.MIN_NEW_BYTES:
            return None
        self._decoded_at = len(self._data)
        try:
            samples, rate = self._decode_all()
        except Exception:
            if final:
                raise
            return None  # not enough bytes for a decodable header yet
        self.sample_rate = rate
        new = samples[self._emitted :]
        if not len(new):
            return None
        self._emitted = len(samples)
        return new.astype("<i2").tobytes(), rate
