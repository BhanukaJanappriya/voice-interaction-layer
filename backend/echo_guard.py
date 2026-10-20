"""Echo guard: tells the assistant's own sound coming back through the
microphone from a person really talking over it.

The problem. The assistant's voice comes out of the speakers and the
microphone hears it again. The browser's echo cancellation has no reference
for sound that the server plays, so the barge in detector (vad.BargeInDetector)
used to take the echo for the user and cut the reply off.

The idea. The server knows exactly what it plays. Every synthesized clip is
put into an EchoReference (the last few seconds of the output at 16 kHz, with
the time each part started playing). For every 30 ms microphone frame the
EchoGuard asks two questions:

  1. Does the microphone look like a delayed, filtered, quieter copy of the
     reference? A normalized correlation of the log band spectrogram (24 mel
     like bands, 100 Hz to 7 kHz, the last 8 frames, so 240 ms) is computed for
     every delay from -50 to 700 ms in 10 ms steps, because the playback and
     device latency is unknown and varies. Band means are removed, so the gain
     and the loudspeaker's colouring (a low pass, a room) do not matter.
  2. Is the microphone louder than that echo can explain? The echo gain per
     band is learned while the user is silent (only from frames that match the
     reference and are not louder than predicted), so the energy the echo would
     produce is known. A frame clearly above it (a factor of EXCESS) holds
     something that is not in the reference: the user.

Verdicts per frame: "echo" (matches the reference, or its energy is explained
by it), "human" (energy the echo cannot explain, or nothing in the reference
resembles it), "quiet" (about the noise floor). The reason string is only for
logging counts. When no reference is playing the guard says "human" with the
reason "no_reference" and the normal VAD decides alone, exactly as before.

A second, cheap guard handles steady sound that is NOT in the reference: a
frame whose total energy has barely moved over 7 to 10 frames (210 to 300 ms)
and whose spectrum is not changing (a tone, a hum, a fan, a test beep) is
reported as "echo" with the reason "stationary". Speech always moves by several
dB at the syllable rate.

Honest limit. Another voice or other media played on the same computer (a video,
a call) is not in the reference and moves like speech. No method that is not a
trained speaker or voice classifier can tell it from a person talking. Headphones
are the real remedy. Only the assistant's own output, and steady sound, are
filtered.

numpy only (no scipy: its DLLs are blocked on this machine).
"""

import threading
import time
import warnings
from collections import Counter, deque
from typing import NamedTuple, Optional

import numpy as np

# ---------------------------------------------------------------- settings
SAMPLE_RATE = 16000
ECHO_FRAME = 480  # 30 ms, the mic frame (same as vad.FRAME_SAMPLES)
ECHO_NFFT = 512
ECHO_GRID = 160  # the reference spectrogram has a column every 10 ms
ECHO_BANDS = 24
ECHO_F_LO = 100.0
ECHO_F_HI = 7000.0
ECHO_MIN_LAG_MS = -50  # the clock estimate can be a little early
ECHO_MAX_LAG_MS = 700  # playback buffer, device and network latency
ECHO_REF_KEEP_S = 4.0  # reference audio kept (the last few seconds)
ECHO_CONTEXT_FRAMES = 8  # frames in one correlation (240 ms)
ECHO_MIN_CONTEXT_FRAMES = 3  # fewer frames than this and nothing is decided yet
ECHO_SIM_LOCK = 0.88  # correlation needed, at one delay for ECHO_LOCK_FRAMES frames in a row, to learn the echo
ECHO_LOCK_FRAMES = 4
ECHO_SIM_LOCK_LONG = 0.78  # or this one for ECHO_LOCK_LONG_FRAMES frames (a reverberant room)
ECHO_LOCK_LONG_FRAMES = 8
ECHO_LOCK_SPREAD = 3  # once the delay is known only delays this many 10 ms steps around it are compared
ECHO_SIM_HIGH = 0.78  # correlation at or above this: looks like the reference
ECHO_PREDICTED_LOUD = 8.0  # the echo this loud over the noise, with a quiet microphone, is not an echo
ECHO_SIM_UPDATE = 0.85  # the gain is learned only from frames at least this alike
ECHO_SIM_STRONG = 0.93  # long and short window both this alike: the echo got louder, not a person
ECHO_EXCESS = 2.0  # mic power over the echo prediction from which a frame is a person
ECHO_EXCESS_BONUS = 1.5  # added to ECHO_EXCESS for a frame that matches the reference perfectly
ECHO_GAIN_JUMP_MAX = 8.0  # a louder echo (volume turned up) is taken for one only up to this excess,
ECHO_GAIN_JUMP_FRAMES = 3  # and only when it keeps matching for this many frames
ECHO_EXCESS_CLEAN = 1.6  # the gain is learned only while the last frames stayed below this
ECHO_GAIN_MIN_REF = 10.0  # the echo gain is measured only where the reference is this far over the noise
ECHO_GAIN_UP = 0.3  # learning rates for the echo power gain (up fast enough, down slowly)
ECHO_GAIN_DOWN = 0.2
ECHO_GAIN_MAX = 16.0
ECHO_REF_ACTIVE_POWER = 1e-5  # mean band power of the reference (about 0.003 RMS) that counts as playing
ECHO_QUIET_RATIO = 3.0  # frame power below this times the noise power is "quiet"
ECHO_STEADY_FRAMES = 10
ECHO_STEADY_MIN_FRAMES = 7
ECHO_STEADY_DB_STD = 1.2  # std of the total frame power in dB below which the sound is steady
ECHO_STEADY_FLUX_DB = 2.0  # and the mean spectrum of the two halves of the window differs less than this (dB)
ECHO_DEFAULT_NOISE_RMS = 0.005

_HOP_SECONDS = ECHO_GRID / SAMPLE_RATE
_MIN_LAG_IDX = int(round(ECHO_MIN_LAG_MS / 1000.0 / _HOP_SECONDS))
_MAX_LAG_IDX = int(round(ECHO_MAX_LAG_MS / 1000.0 / _HOP_SECONDS))
_LAGS = np.arange(_MIN_LAG_IDX, _MAX_LAG_IDX + 1)
_EPS = 1e-12


class Verdict(NamedTuple):
    kind: str  # "echo", "human" or "quiet"
    reason: str  # for logging counts only
    similarity: float = 0.0
    excess: float = 0.0


# --------------------------------------------------------- signal helpers


def _mel(hz):
    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def _inv_mel(mel):
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def _build_filterbank() -> np.ndarray:
    edges = _inv_mel(np.linspace(_mel(ECHO_F_LO), _mel(ECHO_F_HI), ECHO_BANDS + 2))
    freqs = np.fft.rfftfreq(ECHO_NFFT, 1.0 / SAMPLE_RATE)
    bank = np.zeros((ECHO_BANDS, freqs.size))
    for b in range(ECHO_BANDS):
        lo, mid, hi = edges[b], edges[b + 1], edges[b + 2]
        w = np.clip(np.minimum((freqs - lo) / (mid - lo), (hi - freqs) / (hi - mid)), 0.0, None)
        if w.sum() <= 0:
            w[int(np.argmin(np.abs(freqs - mid)))] = 1.0
        bank[b] = w / w.sum()
    return bank.astype(np.float32)


_WINDOW = np.hanning(ECHO_FRAME).astype(np.float32)
_WINDOW_POWER = float(np.sum(_WINDOW**2))
_BANK = _build_filterbank()


def band_power(frames: np.ndarray) -> np.ndarray:
    """(n, 480) float32 frames to (n, 24) band powers. White noise of RMS s gives s squared in every band."""
    spectrum = np.fft.rfft(frames * _WINDOW, ECHO_NFFT, axis=-1)
    power = (spectrum.real**2 + spectrum.imag**2) / _WINDOW_POWER
    return (power @ _BANK.T).astype(np.float32)


def resample_to_16k(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """FFT resampling of a clip to 16 kHz (numpy only)."""
    samples = np.asarray(samples, dtype=np.float32)
    if sample_rate == SAMPLE_RATE or samples.size == 0:
        return samples
    out_len = int(round(samples.size * SAMPLE_RATE / float(sample_rate)))
    if out_len <= 0:
        return np.zeros(0, dtype=np.float32)
    spectrum = np.fft.rfft(samples)
    target_bins = out_len // 2 + 1
    resized = np.zeros(target_bins, dtype=np.complex128)
    keep = min(target_bins, spectrum.size)
    resized[:keep] = spectrum[:keep]
    return (np.fft.irfft(resized, out_len) * (out_len / samples.size)).astype(np.float32)


def _to_float_mono(samples) -> np.ndarray:
    arr = np.asarray(samples)
    if arr.ndim > 1:
        arr = arr.reshape(arr.shape[0], -1).mean(axis=1)
    if arr.dtype.kind in "iu":
        arr = arr.astype(np.float32) / float(np.iinfo(arr.dtype).max + 1 if arr.dtype.kind == "i" else 65536)
    return arr.astype(np.float32, copy=False)


# ------------------------------------------------------------- reference


class EchoReference:
    """The last seconds of what the assistant plays (16 kHz mono float32) as a
    timeline with a log free band power spectrogram column every 10 ms.
    Thread safe: the player thread adds, the audio loop reads."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._origin: Optional[float] = None
        self._reset_locked()

    def _reset_locked(self):
        self._pcm = np.zeros(0, dtype=np.float32)
        self._pcm_base = 0  # absolute sample index of _pcm[0]
        self._end: Optional[int] = None  # absolute sample index after the last sample
        self._rows = np.zeros((0, ECHO_BANDS), dtype=np.float32)
        self._row0 = 0  # grid index of _rows[0]
        self._next_grid = 0  # first grid index whose row is not computed yet

    def clear(self) -> None:
        """Playback was cut off (barge in) or flushed: the reference is gone."""
        with self._lock:
            self._reset_locked()

    @property
    def origin(self) -> Optional[float]:
        return self._origin

    def end_time(self) -> Optional[float]:
        with self._lock:
            if self._end is None or self._origin is None:
                return None
            return self._origin + self._end / SAMPLE_RATE

    def recent(self, now: Optional[float] = None, within: float = ECHO_MAX_LAG_MS / 1000.0 + 0.3) -> bool:
        """True when the reference holds audio that is playing or only just stopped."""
        end = self.end_time()
        if end is None:
            return False
        now = self._clock() if now is None else now
        return end >= now - within

    def add(self, samples, sample_rate: int = SAMPLE_RATE, start_time: Optional[float] = None) -> None:
        """Adds a clip. start_time (the clock time the clip starts playing) puts
        it exactly there, replacing anything later; without it the clip is queued
        behind what is already there (or starts now if that has played out)."""
        pcm = resample_to_16k(_to_float_mono(samples), int(sample_rate))
        if pcm.size == 0:
            return
        with self._lock:
            now = self._clock()
            explicit = start_time is not None
            when = now if start_time is None else float(start_time)
            if self._origin is None:
                self._origin = when
            start = int(round((when - self._origin) * SAMPLE_RATE))
            if self._end is None or start - self._end > int(ECHO_REF_KEEP_S * SAMPLE_RATE):
                self._reset_locked()
                self._pcm_base = start
                self._end = start
                self._next_grid = start // ECHO_GRID + 1
                self._row0 = self._next_grid
            elif not explicit:
                start = max(start, self._end)
            elif start < self._end:
                self._truncate_locked(start)
            if start > self._end:
                self._pcm = np.concatenate([self._pcm, np.zeros(start - self._end, dtype=np.float32)])
                self._end = start
            self._pcm = np.concatenate([self._pcm, pcm])
            self._end += pcm.size
            self._compute_rows_locked()
            self._trim_locked()

    def _truncate_locked(self, start: int) -> None:
        keep = max(0, start - self._pcm_base)
        self._pcm = self._pcm[:keep]
        self._end = self._pcm_base + keep
        last_grid = self._end // ECHO_GRID
        self._next_grid = min(self._next_grid, last_grid + 1)
        rows = max(0, self._next_grid - self._row0)
        self._rows = self._rows[:rows]

    def _compute_rows_locked(self) -> None:
        last_grid = self._end // ECHO_GRID
        if last_grid < self._next_grid:
            return
        grids = np.arange(self._next_grid, last_grid + 1)
        ends = grids * ECHO_GRID - self._pcm_base  # index in _pcm after the window's last sample
        idx = ends[:, None] - ECHO_FRAME + np.arange(ECHO_FRAME)[None, :]
        valid = (idx >= 0) & (idx < self._pcm.size)
        frames = np.where(valid, self._pcm[np.clip(idx, 0, max(0, self._pcm.size - 1))] if self._pcm.size else 0.0, 0.0)
        self._rows = np.concatenate([self._rows, band_power(frames.astype(np.float32))])
        self._next_grid = last_grid + 1

    def _trim_locked(self) -> None:
        keep_from = self._end - int((ECHO_REF_KEEP_S + 0.2) * SAMPLE_RATE)
        if keep_from > self._pcm_base + ECHO_FRAME:
            drop = keep_from - self._pcm_base
            self._pcm = self._pcm[drop:]
            self._pcm_base += drop
        first_grid = (self._pcm_base + ECHO_FRAME) // ECHO_GRID + 1
        if first_grid > self._row0 and self._rows.shape[0] > 0:
            cut = min(first_grid - self._row0, self._rows.shape[0])
            self._rows = self._rows[cut:]
            self._row0 += cut

    def rows_at(self, grid_idx: np.ndarray) -> np.ndarray:
        """Band powers of the reference windows ending at the given grid indices
        (zeros where there is no reference)."""
        out = np.zeros(grid_idx.shape + (ECHO_BANDS,), dtype=np.float32)
        with self._lock:
            rel = grid_idx - self._row0
            ok = (rel >= 0) & (rel < self._rows.shape[0])
            if ok.any():
                out[ok] = self._rows[rel[ok]]
        return out


_shared_reference: Optional[EchoReference] = None
_shared_lock = threading.Lock()


def shared_reference() -> EchoReference:
    """The reference the server's own player (tts.py) feeds."""
    global _shared_reference
    with _shared_lock:
        if _shared_reference is None:
            _shared_reference = EchoReference()
        return _shared_reference


# ----------------------------------------------------------------- guard


class EchoGuard:
    """Judges microphone frames against an EchoReference. One instance per
    listening stream (a reply on /ws/voice, a LiveKit session)."""

    def __init__(
        self,
        reference: Optional[EchoReference] = None,
        noise_rms: Optional[float] = None,
        clock=time.monotonic,
    ):
        self.reference = reference if reference is not None else shared_reference()
        self._clock = clock
        rms = ECHO_DEFAULT_NOISE_RMS if not noise_rms else max(float(noise_rms), 0.002)
        self._noise = np.full(ECHO_BANDS, rms * rms, dtype=np.float32)
        self._gain: Optional[np.ndarray] = None
        self.counts: Counter = Counter()
        self.reset_stream()

    # -- state ---------------------------------------------------------
    def reset_stream(self) -> None:
        """Forget the mic timeline (after a gap in the audio). The learned noise
        and echo gain stay."""
        self._carry = np.zeros(0, dtype=np.float32)
        self._fed = 0
        self._offset: Optional[float] = None
        self._hist: deque = deque(maxlen=ECHO_CONTEXT_FRAMES)
        self._steady: deque = deque(maxlen=ECHO_STEADY_FRAMES)
        self._recent_excess: deque = deque(maxlen=3)
        self._lag: Optional[int] = None  # index into _LAGS of the learned echo delay
        self._louder_streak = 0
        self._matches: deque = deque(maxlen=ECHO_LOCK_LONG_FRAMES)

    def reset_framing(self) -> None:
        """Drop only the partial frame (the detector does the same on reset)."""
        self._carry = np.zeros(0, dtype=np.float32)

    def set_noise_floor_rms(self, rms: float) -> None:
        if rms and not self.counts:
            self._noise = np.full(ECHO_BANDS, max(float(rms), 0.002) ** 2, dtype=np.float32)

    @property
    def echo_gain(self) -> Optional[np.ndarray]:
        return None if self._gain is None else self._gain.copy()

    def stats(self) -> dict:
        return {f"{kind}:{reason}": n for (kind, reason), n in sorted(self.counts.items())}

    def reference_recent(self, now: Optional[float] = None) -> bool:
        return self.reference.recent(self._clock() if now is None else now)

    # -- streaming ------------------------------------------------------
    def process_chunk(self, chunk: np.ndarray, now: Optional[float] = None) -> list:
        """Takes the next microphone audio (any length, float32 mono 16 kHz) that
        ended at `now`, returns one Verdict per complete 30 ms frame."""
        chunk = np.asarray(chunk, dtype=np.float32)
        if chunk.size == 0:
            return []
        now = self._clock() if now is None else float(now)
        data = np.concatenate([self._carry, chunk])
        start_abs = self._fed - self._carry.size
        self._fed += chunk.size
        observed = now - self._fed / SAMPLE_RATE
        if self._offset is None or observed < self._offset:
            self._offset = observed  # the earliest arrival is the least delayed one
        else:
            self._offset += 0.01 * (observed - self._offset)
        count = data.size // ECHO_FRAME
        self._carry = data[count * ECHO_FRAME :]
        verdicts = []
        for i in range(count):
            frame_end = self._offset + (start_abs + (i + 1) * ECHO_FRAME) / SAMPLE_RATE
            verdicts.append(self.process_frame(data[i * ECHO_FRAME : (i + 1) * ECHO_FRAME], frame_end))
        return verdicts

    def process_frame(self, frame: np.ndarray, end_time: float) -> Verdict:
        verdict = self._judge(frame, end_time)
        self.counts[(verdict.kind, verdict.reason)] += 1
        return verdict

    # -- the decision ---------------------------------------------------
    def _judge(self, frame: np.ndarray, end_time: float) -> Verdict:
        power = band_power(frame[None, :].astype(np.float32))[0]
        total = float(power.sum())
        noise_total = float(self._noise.sum())
        self._hist.append((end_time, power))

        quiet = total < ECHO_QUIET_RATIO * noise_total
        if quiet:
            # Only quiet frames teach the noise floor: down quickly, up slowly.
            rate = np.where(power < self._noise, 0.1, 0.05).astype(np.float32)
            self._noise = np.maximum(self._noise + rate * (power - self._noise), 1e-9)
            self._steady.clear()
            return Verdict("quiet", "below_noise")

        steady = self._is_steady(power, total)
        if steady:
            return Verdict("echo", "stationary")

        reference = self.reference
        origin = reference.origin
        if origin is None or not reference.recent(end_time):
            return Verdict("human", "no_reference")
        if len(self._hist) < ECHO_MIN_CONTEXT_FRAMES:
            return Verdict("echo", "warming_up")

        times = np.array([t for t, _ in self._hist], dtype=np.float64)
        mic = np.stack([p for _, p in self._hist]).astype(np.float64)  # (K, B)
        grid = np.round((times - origin) / _HOP_SECONDS).astype(np.int64)
        windows = grid[None, :] - _LAGS[:, None]  # (L, K)
        ref = reference.rows_at(windows).astype(np.float64)  # (L, K, B)
        if ref[:, -1, :].mean(axis=1).max() < ECHO_REF_ACTIVE_POWER:
            return Verdict("human", "no_reference")

        sim, gfit = self._similarity(mic, ref)
        full = mic.shape[0] >= ECHO_CONTEXT_FRAMES
        best = int(np.argmax(sim))
        sim_best = float(sim[best])
        # A step in the microphone (a person starting to talk) can resemble any
        # onset in the reference. A real echo also keeps one gain over the window.
        consistent = self._energy_consistent(mic, ref[best])

        # Learn the echo delay: one delay, a close match, several frames in a row (a
        # very close match for a few frames, or a good one for twice as long, as a
        # reverberant room gives).
        if full:
            self._matches.append((best, sim_best if consistent else 0.0))
            if self._locks(ECHO_LOCK_FRAMES, ECHO_SIM_LOCK) or self._locks(ECHO_LOCK_LONG_FRAMES, ECHO_SIM_LOCK_LONG):
                self._lag = best
                if self._gain is None:
                    self._gain = self._robust_gain(mic, ref[best]).astype(np.float32)

        if self._lag is None:
            # The echo has not been learned yet: only a good match is taken for the echo.
            if sim_best >= ECHO_SIM_HIGH and consistent:
                return Verdict("echo", "matching", sim_best, 0.0)
            return Verdict("human", "unmatched", sim_best, 0.0)

        lo, hi = max(0, self._lag - ECHO_LOCK_SPREAD), min(_LAGS.size, self._lag + ECHO_LOCK_SPREAD + 1)
        best = lo + int(np.argmax(sim[lo:hi]))
        sim_best = float(sim[best])
        if sim_best >= ECHO_SIM_UPDATE:
            self._lag = best  # follows a slow drift of the delay
        lo, hi = max(0, best - 1), min(_LAGS.size, best + 2)
        ref_now = ref[lo:hi, -1, :].max(axis=0)

        predicted = self._gain * ref_now + self._noise
        excess = total / max(float(predicted.sum()), _EPS)
        self._recent_excess.append(excess)

        # The better the microphone still resembles the reference, the more extra energy
        # (a reverberant room) is put down to the echo.
        limit = ECHO_EXCESS + ECHO_EXCESS_BONUS * float(np.clip((sim_best - ECHO_SIM_HIGH) / (0.95 - ECHO_SIM_HIGH), 0.0, 1.0))
        if excess < limit:
            self._louder_streak = 0
            if sim_best >= ECHO_SIM_HIGH:
                self._learn(mic, ref[best], sim_best)
                return Verdict("echo", "matches_reference", sim_best, excess)
            return Verdict("echo", "explained_by_echo", sim_best, excess)

        # Louder than the echo predicts. Either a person, or the echo itself got louder
        # (the volume was turned up). A person starts abruptly and the match falls
        # within a few frames; a louder echo keeps matching, frame after frame.
        louder_echo = (
            excess <= ECHO_GAIN_JUMP_MAX
            and sim_best >= ECHO_SIM_STRONG
            and self._short_similarity(mic, ref, best) >= 0.9
        )
        self._louder_streak = self._louder_streak + 1 if louder_echo else 0
        if self._louder_streak >= ECHO_GAIN_JUMP_FRAMES:
            fit = self._robust_gain(mic, ref[best]).astype(np.float32)
            self._gain = self._gain + 0.5 * np.maximum(fit - self._gain, 0.0)
            return Verdict("echo", "gain_changed", sim_best, excess)
        return Verdict("human", "above_echo", sim_best, excess)

    def _energy_consistent(self, mic: np.ndarray, ref_at_lag: np.ndarray) -> bool:
        """False when the microphone was quiet in frames where an echo of this
        reference would have been loud: a person starting to talk looks like the
        onset of any reference, but before the onset the microphone was silent."""
        noise_sum = float(self._noise.sum())
        mic_total = mic.sum(axis=1)
        ref_total = ref_at_lag.sum(axis=1)
        excess = np.maximum(mic_total - noise_sum, 0.0).sum()
        gain = excess / (ref_total.sum() + _EPS)
        predicted = noise_sum + gain * ref_total
        silent_but_predicted_loud = (mic_total < ECHO_QUIET_RATIO * noise_sum) & (predicted > ECHO_PREDICTED_LOUD * noise_sum)
        return int(silent_but_predicted_loud.sum()) <= 1

    def _locks(self, frames: int, threshold: float) -> bool:
        if len(self._matches) < frames:
            return False
        recent = list(self._matches)[-frames:]
        lags = [lag for lag, _ in recent]
        return min(sim for _, sim in recent) >= threshold and max(lags) - min(lags) <= 2

    def _robust_gain(self, mic: np.ndarray, ref_at_lag: np.ndarray) -> np.ndarray:
        """Per band power gain of the echo: the median over the frames of
        (mic minus noise) over reference, so a few frames that also hold the
        person do not inflate it. Only where the reference is well above the noise
        (elsewhere the ratio is meaningless); bands without such frames take the
        median of the others (or the gain known so far)."""
        noise = self._noise.astype(np.float64)
        ratio = np.maximum(mic - noise, 0.0) / (ref_at_lag + _EPS)
        ratio = np.where(ref_at_lag > ECHO_GAIN_MIN_REF * noise, ratio, np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # a band with no usable frame gives nan on purpose
            gain = np.nanmedian(ratio, axis=0)
        known = np.isfinite(gain)
        if known.any():
            fallback = float(np.median(gain[known]))
        elif self._gain is not None:
            return self._gain.astype(np.float64)
        else:
            fallback = 0.0
        return np.clip(np.where(known, gain, fallback), 0.0, ECHO_GAIN_MAX)

    def _learn(self, mic: np.ndarray, ref_at_lag: np.ndarray, sim: float) -> None:
        if sim < ECHO_SIM_UPDATE or len(self._recent_excess) < 3 or max(self._recent_excess) >= ECHO_EXCESS_CLEAN:
            return
        gain_fit = self._robust_gain(mic, ref_at_lag)
        rate = np.where(gain_fit > self._gain, ECHO_GAIN_UP, ECHO_GAIN_DOWN).astype(np.float32)
        self._gain = self._gain + rate * (gain_fit.astype(np.float32) - self._gain)

    def _similarity(self, mic: np.ndarray, ref: np.ndarray):
        """Correlation of the mic log spectrogram with the reference at every lag,
        and the per band power gain that fits the mic to the reference."""
        noise = self._noise.astype(np.float64)
        excess = np.maximum(mic - noise, 0.0).sum(axis=0)  # (B,)
        ref_sum = ref.sum(axis=1)  # (L, B)
        gfit = excess[None, :] / (ref_sum + _EPS)
        model = gfit[:, None, :] * ref + noise
        log_mic = np.log10(mic + _EPS)
        log_mic = log_mic - log_mic.mean(axis=0, keepdims=True)
        log_model = np.log10(model + _EPS)
        log_model = log_model - log_model.mean(axis=1, keepdims=True)
        numer = np.einsum("kb,lkb->l", log_mic, log_model)
        denom = np.sqrt(np.sum(log_mic**2) * np.sum(log_model**2, axis=(1, 2))) + _EPS
        sim = numer / denom
        if np.sum(log_mic**2) < 1e-6:
            sim = np.zeros_like(sim)
        return sim, gfit

    def _short_similarity(self, mic: np.ndarray, ref: np.ndarray, lag_index: int) -> float:
        k = min(3, mic.shape[0])
        noise = self._noise.astype(np.float64)
        m = mic[-k:]
        r = ref[lag_index, -k:, :]
        excess = np.maximum(m - noise, 0.0).sum(axis=0)
        g = excess / (r.sum(axis=0) + _EPS)
        log_mic = np.log10(m + _EPS)
        log_mic -= log_mic.mean(axis=0, keepdims=True)
        log_model = np.log10(g * r + noise + _EPS)
        log_model -= log_model.mean(axis=0, keepdims=True)
        denom = np.sqrt(np.sum(log_mic**2) * np.sum(log_model**2)) + _EPS
        return float(np.sum(log_mic * log_model) / denom)

    def _is_steady(self, power: np.ndarray, total: float) -> bool:
        """A tone, hum, fan or beep: the total energy hardly moves and the average
        spectrum of the first half of the last 210 to 300 ms equals that of the second
        half. (Noise like a fan flickers from frame to frame, so the halves are
        averaged; speech changes by many dB at the syllable rate.)"""
        self._steady.append((10.0 * np.log10(total + _EPS), power.copy()))
        if len(self._steady) < ECHO_STEADY_MIN_FRAMES:
            return False
        totals = np.array([t for t, _ in self._steady])
        if float(np.std(totals)) >= ECHO_STEADY_DB_STD:
            return False
        powers = np.stack([p for _, p in self._steady])
        half = powers.shape[0] // 2
        first = 10.0 * np.log10(powers[:half].mean(axis=0) + _EPS)
        second = 10.0 * np.log10(powers[half:].mean(axis=0) + _EPS)
        active = powers[-1] > 4.0 * self._noise
        if not active.any():
            return True
        return float(np.mean(np.abs(first - second)[active])) < ECHO_STEADY_FLUX_DB
