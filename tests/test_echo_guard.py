"""Echo guard: the assistant's own voice coming back through the microphone must
never count as the user, a person talking over it must.

Everything is synthetic or taken from the two TTS made speech files in
frontend/avatar/assets (no microphone, no speakers, no network). Time is
simulated: the reference starts playing at T0 on a fake clock, the microphone
chunks (0.25 s, like the page sends) carry the simulated arrival time.

The microphone model: mic = gain * lowpass(reference delayed by `delay`) + room
noise (+ a person). Echo trials use random gain, delay, loudspeaker low pass
and noise; person trials use another voice that starts at a random moment while
the reference plays, at the level of a person near the microphone.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

SR = 16000
CHUNK = 4000  # 250 ms, what the page sends
ASSETS = Path(__file__).resolve().parent.parent / "frontend" / "avatar" / "assets"
AMBIENT = 0.004  # room noise RMS
T0 = 100.0  # the moment the reference starts playing (fake clock)
REVERB_RATIO = (0.05, 0.5)  # reflected to direct energy of the room: 13 dB to 3 dB direct to reverberant


# ---------------------------------------------------------------- audio

def _load(name):
    import soundfile as sf

    data, rate = sf.read(str(ASSETS / name), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    from echo_guard import resample_to_16k

    data = resample_to_16k(data, rate)
    return data / np.sqrt(np.mean(data**2))  # RMS 1, scaled where used


_SPEECH = {}


def speech_bank():
    if not _SPEECH:
        _SPEECH["sample"] = _load("sample.wav")
        _SPEECH["question"] = _load("test_question.wav")
    return _SPEECH


def synth_speech(seconds, seed):
    """Syllables with a moving pitch, formant like filtering of the harmonics,
    short pauses and the odd fricative burst. RMS 1."""
    rng = np.random.default_rng(seed)
    out = []
    total = 0
    f0 = rng.uniform(95, 230)
    while total < seconds * SR:
        dur = rng.uniform(0.10, 0.28)
        n = int(dur * SR)
        t = np.arange(n) / SR
        f1, f2, f3 = rng.uniform(300, 800), rng.uniform(900, 2300), rng.uniform(2400, 3300)
        f0 = float(np.clip(f0 * rng.uniform(0.9, 1.1), 85, 260))
        phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(2, 6) * t)) / SR)
        sig = np.zeros(n)
        for k in range(1, int(4500 / f0)):
            f = f0 * k
            amp = (1.0 / k) * (
                1
                + 5 * np.exp(-(((f - f1) / 130) ** 2))
                + 4 * np.exp(-(((f - f2) / 200) ** 2))
                + 2 * np.exp(-(((f - f3) / 300) ** 2))
            )
            sig += amp * np.sin(k * phase)
        env = np.sin(np.pi * t / dur) ** 0.7
        sig = sig * env
        if rng.random() < 0.25:  # fricative
            hiss = rng.standard_normal(n) * env
            hiss = np.convolve(hiss, np.array([1, -1.6, 0.8]), mode="same")
            sig = sig / (np.sqrt(np.mean(sig**2)) + 1e-9) * 0.4 + hiss / (np.sqrt(np.mean(hiss**2)) + 1e-9) * 0.9
        out.append(sig / (np.sqrt(np.mean(sig**2)) + 1e-9) * rng.uniform(0.6, 1.2))
        pause = int(rng.uniform(0.01, 0.09) * SR)
        out.append(np.zeros(pause))
        total += n + pause
    sig = np.concatenate(out)[: int(seconds * SR)]
    return (sig / np.sqrt(np.mean(sig**2))).astype(np.float32)


def speech_segment(source, seconds, rng):
    """A random slice of a speech source (name in the bank, or "synth")."""
    n = int(seconds * SR)
    if source == "synth":
        return synth_speech(seconds, int(rng.integers(1 << 30)))
    data = speech_bank()[source]
    start = int(rng.integers(0, max(1, data.size - n)))
    seg = data[start : start + n]
    if seg.size < n:
        seg = np.pad(seg, (0, n - seg.size))
    return (seg / (np.sqrt(np.mean(seg**2)) + 1e-9)).astype(np.float32)


def lowpass(x, cutoff, taps=65):
    if cutoff >= 7900:
        return x
    n = np.arange(taps) - taps // 2
    h = np.sinc(2 * cutoff / SR * n) * np.hamming(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same").astype(np.float32)


def delayed(x, delay_s, length):
    d = int(round(delay_s * SR))
    out = np.zeros(length, dtype=np.float32)
    seg = x[: max(0, length - d)]
    out[d : d + seg.size] = seg
    return out


def room_noise(n, rng, level=AMBIENT):
    return (rng.standard_normal(n) * level).astype(np.float32)


# ------------------------------------------------------------ the rig


class Rig:
    """A reference, a guard and a microphone stream on a fake clock."""

    def __init__(self, ref, noise_rms=AMBIENT, jitter=0.0, arrival=0.03, seed=0):
        from echo_guard import EchoGuard, EchoReference

        self.now = T0
        self.clock = lambda: self.now
        self.reference = EchoReference(clock=self.clock)
        self.guard = EchoGuard(self.reference, noise_rms=noise_rms, clock=self.clock)
        self.ref = ref
        self.rng = np.random.default_rng(seed)
        self.jitter = jitter
        self.arrival = arrival  # the page sends a chunk this long after it was captured
        self.reference.add(ref, SR, start_time=T0)

    def arrival_time(self, end_sample):
        t = T0 + end_sample / SR + self.arrival
        if self.jitter:
            t += float(self.rng.uniform(0, self.jitter))
        return t

    def run(self, mic):
        """Feeds the microphone in 250 ms chunks, returns the verdict per 30 ms frame."""
        verdicts = []
        for i in range(0, mic.size, CHUNK):
            chunk = mic[i : i + CHUNK]
            self.now = self.arrival_time(i + chunk.size)
            verdicts.extend(self.guard.process_chunk(chunk, self.now))
        return verdicts


def human_frames(verdicts, start=0, stop=None):
    return sum(1 for v in verdicts[start:stop] if v.kind == "human")


def reverberate(x, rt, rng):
    """A room: the direct sound plus a decaying noise tail (rt = time to -60 dB)."""
    if rt <= 0:
        return x
    n = int(rt * SR)
    t = np.arange(n) / SR
    h = np.zeros(n + 1)
    h[0] = 1.0
    tail = rng.standard_normal(n) * np.exp(-6.9 * t / rt)
    # reflected to direct energy ratio between 0.05 and 1 (direct to reverberant ratio 13 dB to 0 dB)
    h[1:] = tail / np.sqrt(np.sum(tail**2)) * np.sqrt(rng.uniform(*REVERB_RATIO))
    size = 1 << int(np.ceil(np.log2(x.size + h.size)))
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[: x.size].astype(np.float32)


def make_echo_mic(ref, n, gain, delay, cutoff, rng, noise=AMBIENT, reverb=0.0):
    echo = reverberate(lowpass(delayed(ref, delay, n), cutoff), reverb, rng) * gain
    return echo + room_noise(n, rng, noise)


def trial_reference(rng, seconds=4.0, level=None):
    source = ["sample", "question", "synth"][int(rng.integers(3))]
    level = level if level is not None else float(rng.uniform(0.08, 0.2))
    return speech_segment(source, seconds, rng) * level, source


def trial_human(rng, seconds, level, ref_source):
    others = [s for s in ("sample", "question", "synth") if s != ref_source]
    return speech_segment(others[int(rng.integers(len(others)))], seconds, rng) * level


# ---------------------------------------------------------- the detector

TRIAL_SECONDS = 3.2


def calibrated_vad():
    from vad import VoiceActivityDetector

    vad = VoiceActivityDetector()
    vad.calibrate_noise_floor(room_noise(SR, np.random.default_rng(5)))
    return vad


def detect(rig, mic, guard=True, chunk=CHUNK):
    """Feeds the mic through a BargeInDetector. Returns the audio time (seconds
    from the start of the stream) at which the reply would be cut off, or None."""
    from vad import BargeInDetector

    detector = BargeInDetector(calibrated_vad(), echo_guard=rig.guard if guard else False)
    for i in range(0, mic.size, chunk):
        piece = mic[i : i + chunk]
        rig.now = rig.arrival_time(i + piece.size)
        if detector.feed(piece, rig.now) is not None:
            return (i + piece.size) / SR
    return None


def echo_trial(rng):
    ref, source = trial_reference(rng, TRIAL_SECONDS)
    params = dict(
        gain=float(rng.uniform(0.05, 1.0)),
        delay=float(rng.uniform(0.0, 0.5)),
        cutoff=float(rng.choice([3500, 5000, 7900])),
        noise=float(rng.uniform(0.002, 0.01)),
        reverb=float(rng.uniform(0.0, 0.4)) if rng.random() < 0.5 else 0.0,
    )
    rig = Rig(ref, jitter=0.04, arrival=float(rng.uniform(0.0, 0.1)), seed=int(rng.integers(1 << 30)))
    mic = make_echo_mic(
        ref, ref.size, params["gain"], params["delay"], params["cutoff"], rng, params["noise"], params["reverb"]
    )
    return ref, source, rig, mic, params


def rms(x):
    return float(np.sqrt(np.mean(np.square(x))))


# --------------------------------------------------------------- tests


@pytest.mark.parametrize("source", ["sample", "question", "synth"])
@pytest.mark.parametrize("gain", [0.05, 0.2, 0.5, 1.0])
@pytest.mark.parametrize("delay", [0.0, 0.12, 0.3, 0.5])
def test_delayed_attenuated_filtered_noisy_copy_is_never_human(source, gain, delay):
    rng = np.random.default_rng(int(gain * 100) + int(delay * 1000) + len(source))
    ref = speech_segment(source, 3.5, rng) * 0.15
    rig = Rig(ref, jitter=0.03, arrival=0.05, seed=3)
    mic = make_echo_mic(ref, ref.size, gain, delay, 4500, rng, noise=0.006)
    verdicts = rig.run(mic)
    assert human_frames(verdicts) <= 8, rig.guard.stats()  # the detector needs 10 in a row
    assert detect(Rig(ref, jitter=0.03, arrival=0.05, seed=3), mic) is None


def test_other_voice_over_the_reference_is_human_within_the_confirmation_time():
    """No echo at all (the person is on headphones, or far from the speakers)."""
    rng = np.random.default_rng(11)
    latencies = []
    for k in range(6):
        ref = speech_segment("sample", 4.0, rng) * 0.15
        onset = 1.0 + 0.13 * k
        mic = room_noise(ref.size, rng)
        voice = speech_segment("question", ref.size / SR - onset, rng) * 0.1
        mic[int(onset * SR) : int(onset * SR) + voice.size] += voice
        at = detect(Rig(ref, jitter=0.02), mic, chunk=480)  # frame by frame: the pure decision latency
        plain = detect(Rig(ref, jitter=0.02), mic, guard=False, chunk=480)
        assert at is not None, "the person was not detected"
        latencies.append((at - onset, plain - onset))
    on, off = np.mean([a for a, _ in latencies]), np.mean([b for _, b in latencies])
    print(f"onset to cut off, 30 ms chunks: {on * 1000:.0f} ms with the guard, {off * 1000:.0f} ms without it")
    # the 300 ms confirmation plus the speech file's own soft start; the guard adds at most a little
    assert all(a - b <= 0.3 for a, b in latencies)
    assert on <= 0.55


def test_echo_plus_a_louder_voice_is_human():
    rng = np.random.default_rng(12)
    for k in range(8):
        ref = speech_segment("sample", 4.0, rng) * 0.15
        mic = make_echo_mic(ref, ref.size, 0.4, 0.2 + 0.05 * k, 5000, rng)
        onset = 1.6
        echo_level = rms(mic)
        voice = speech_segment("question", ref.size / SR - onset, rng)
        voice *= max(2.0 * echo_level, 0.06)
        mic[int(onset * SR) : int(onset * SR) + voice.size] += voice
        at = detect(Rig(ref, jitter=0.02, seed=k), mic)
        assert at is not None, f"trial {k}: a voice louder than the echo must cut the reply off"
        assert at - onset <= 1.0


def test_noise_and_noise_plus_echo_are_not_human():
    rng = np.random.default_rng(13)
    ref = speech_segment("sample", 4.0, rng) * 0.15
    for level in (0.004, 0.02, 0.08):
        noise_only = room_noise(ref.size, rng, level)
        # a few frames before the steadiness is known; the detector needs 10 in a row
        assert human_frames(Rig(ref, seed=1).run(noise_only)) <= 8, f"noise {level}"
        assert detect(Rig(ref, seed=1), noise_only) is None
        if level > 0.03:
            continue  # noise as loud as speech on top of an echo is beyond what this guard separates (see the report)
        both = make_echo_mic(ref, ref.size, 0.5, 0.2, 5000, rng, noise=0.004) + room_noise(ref.size, rng, level)
        assert detect(Rig(ref, seed=1), both) is None, f"echo plus noise {level}"


def test_after_the_reference_stops_speech_is_detected_as_before():
    rng = np.random.default_rng(14)
    ref = speech_segment("sample", 2.0, rng) * 0.15
    total = int(6.0 * SR)
    mic = make_echo_mic(ref, total, 0.5, 0.2, 5000, rng)
    onset = 4.2  # well after the reference (and its latency window) is over
    voice = speech_segment("question", 1.6, rng) * 0.1
    mic[int(onset * SR) : int(onset * SR) + voice.size] += voice
    guarded = detect(Rig(ref, jitter=0.02), mic)
    # the same speech without the guard (cut after the echo, which a plain detector cannot tell from a person)
    cut = int(3.5 * SR)
    plain = detect(Rig(ref, jitter=0.02), mic[cut:], guard=False)
    assert guarded is not None and plain is not None
    plain += 3.5
    print(f"after the reference: guard on {guarded - onset:.2f} s, guard off {plain - onset:.2f} s after the onset")
    assert abs(guarded - plain) <= 0.3
    verdicts = Rig(ref).run(mic)
    assert any(v.reason == "no_reference" and v.kind == "human" for v in verdicts[int(onset / 0.03) :])


def test_without_any_reference_the_old_behaviour_is_kept():
    from echo_guard import EchoGuard, EchoReference

    rng = np.random.default_rng(15)
    guard = EchoGuard(EchoReference(), noise_rms=AMBIENT)
    voice = speech_segment("question", 2.0, rng) * 0.1
    verdicts = guard.process_chunk(np.concatenate([room_noise(8000, rng), voice]), now=50.0)
    kinds = {(v.kind, v.reason) for v in verdicts}
    assert ("human", "no_reference") in kinds
    assert not any(v.reason in ("matches_reference", "explained_by_echo", "above_echo") for v in verdicts)


def test_cancel_clears_the_reference():
    from echo_guard import EchoGuard, EchoReference

    rng = np.random.default_rng(16)
    ref = speech_segment("sample", 3.0, rng) * 0.15
    clock = {"t": T0}
    reference = EchoReference(clock=lambda: clock["t"])
    reference.add(ref, SR, start_time=T0)
    clock["t"] = T0 + 1.0
    assert reference.recent()
    reference.clear()
    assert not reference.recent()
    guard = EchoGuard(reference, noise_rms=AMBIENT, clock=lambda: clock["t"])
    mic = make_echo_mic(ref, ref.size, 0.5, 0.1, 5000, rng)[: int(2.0 * SR)]
    verdicts = guard.process_chunk(mic, now=T0 + 2.0)
    assert all(v.reason != "matches_reference" for v in verdicts)


def test_real_player_feeds_the_reference_and_cancel_clears_it(monkeypatch):
    import sys
    import threading

    pytest.importorskip("pyttsx3")
    import echo_guard
    import tts
    from test_barge_in import FakeSoundDevice

    fake_sd = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    reference = echo_guard.shared_reference()
    reference.clear()
    clip = (np.sin(2 * np.pi * 300 * np.arange(24000) / 24000) * 12000).astype(np.int16)  # 1 s at 24 kHz
    monkeypatch.setattr(tts, "_prepare_sentence", lambda s, l, v: (lambda: tts._play(clip, 24000)))

    tts.speak_stream(["one."], "en", "female_natural")
    assert reference.recent(), "every played clip goes into the reference"

    cancel = threading.Event()
    started = []
    thread = threading.Thread(
        target=lambda: tts.speak_stream(["two.", "three."], "en", "female_natural", started.append, cancel)
    )
    thread.start()
    deadline = time.monotonic() + 3
    while not started and time.monotonic() < deadline:
        time.sleep(0.005)
    time.sleep(0.1)
    assert reference.recent()
    cancel.set()
    thread.join(3)
    assert not reference.recent(), "a cancelled (flushed) playback leaves no reference behind"


@pytest.mark.parametrize("kind", ["tone", "hum", "beep_train", "fan"])
@pytest.mark.parametrize("with_reference", [True, False])
def test_stationary_sound_is_not_human(kind, with_reference):
    rng = np.random.default_rng(17)
    n = int(5.0 * SR)
    t = np.arange(n) / SR
    if kind == "tone":
        sig = 0.12 * np.sin(2 * np.pi * 440 * t)
    elif kind == "hum":
        sig = sum(0.05 / k * np.sin(2 * np.pi * 50 * k * t) for k in range(1, 12))
    elif kind == "beep_train":
        sig = 0.12 * np.sin(2 * np.pi * 900 * t)
    else:
        sig = lowpass(rng.standard_normal(n).astype(np.float32) * 0.15, 3000)
    mic = (sig + room_noise(n, rng)).astype(np.float32)
    ref = speech_segment("sample", 5.0, rng) * 0.15 if with_reference else np.zeros(n, dtype=np.float32)
    verdicts = Rig(ref, jitter=0.02).run(mic)
    assert human_frames(verdicts, start=10) <= 3
    assert detect(Rig(ref, jitter=0.02), mic) is None


def test_real_speech_is_not_taken_for_stationary():
    from echo_guard import EchoGuard, EchoReference

    rng = np.random.default_rng(18)
    guard = EchoGuard(EchoReference(), noise_rms=AMBIENT)
    voice = speech_segment("sample", 4.0, rng) * 0.1
    verdicts = guard.process_chunk(voice, now=10.0)
    stationary = sum(1 for v in verdicts if v.reason == "stationary")
    assert stationary / len(verdicts) < 0.05


def test_livekit_session_block_filter():
    """The session asks the guard for every interruptible block, but only when it has published speech lately."""
    pytest.importorskip("livekit")
    from echo_guard import EchoReference
    from livekit_transport.session import LiveKitVoiceSession

    session = object.__new__(LiveKitVoiceSession)
    session._echo_reference = EchoReference()
    session._echo_guard = None
    session._vad = type("V", (), {"noise_floor_rms": 0.01})()
    rng = np.random.default_rng(19)
    voice = speech_segment("question", 3.0, rng) * 0.1
    block = 1600
    assert all(session._block_is_human(voice[i : i + block]) for i in range(0, block * 5, block)), "no reference: old behaviour"
    # the session publishes speech (24 kHz int16) and the microphone only hears it back, in real time
    ref = speech_segment("sample", 4.0, rng) * 0.15
    ref24 = np.interp(np.arange(0, ref.size * 1.5) / 1.5, np.arange(ref.size), ref)
    session._echo_reference.add((ref24 * 32767).astype(np.int16), 24000)
    echo = make_echo_mic(ref, ref.size, 0.5, 0.15, 5000, rng, noise=0.004)
    passed = blocks = 0
    started = time.monotonic()
    for i in range(0, block * 25, block):
        # real time: block i ends (i + block) / SR seconds after the reference started playing
        time.sleep(max(0.0, started + (i + block) / SR - time.monotonic()))
        blocks += 1
        passed += bool(session._block_is_human(echo[i : i + block]))
    print(f"livekit session: {passed} of {blocks} echo only blocks passed the guard")
    assert passed <= blocks * 0.2


# ------------------------------------------------------- randomized rates


def test_false_interrupt_rate_over_200_random_echo_trials():
    rng = np.random.default_rng(2026)
    false = []
    human_total = frames_total = 0
    for k in range(200):
        ref, source, rig, mic, params = echo_trial(rng)
        verdicts = rig.run(mic)
        human_total += human_frames(verdicts)
        frames_total += len(verdicts)
        if detect(Rig(ref, jitter=0.04, arrival=rig.arrival, seed=k), mic) is not None:
            false.append((source, {name: round(value, 3) for name, value in params.items()}))
    rate = len(false) / 200
    print(
        f"false interrupts: {len(false)} of 200 echo trials ({rate * 100:.1f} %); "
        f"frames judged human {human_total} of {frames_total} ({human_total / frames_total * 100:.2f} %)"
    )
    for item in false[:5]:
        print("  false interrupt:", item)
    assert rate < 0.02


def test_false_interrupt_rate_in_a_very_reverberant_room(monkeypatch):
    """Direct to reverberant ratio between 3 dB and 0 dB with a tail up to 0.4 s: the hard case.
    Reported, with a looser bound than the normal rooms."""
    monkeypatch.setattr(sys.modules[__name__], "REVERB_RATIO", (0.5, 1.0))
    rng = np.random.default_rng(2029)
    false = 0
    for k in range(100):
        ref, source, rig, mic, params = echo_trial(rng)
        mic = make_echo_mic(ref, ref.size, params["gain"], params["delay"], params["cutoff"], rng, params["noise"], 0.1 + 0.3 * float(rng.random()))
        if detect(Rig(ref, jitter=0.04, arrival=rig.arrival, seed=k), mic) is not None:
            false += 1
    print(f"false interrupts in a very reverberant room: {false} of 100")
    assert false <= 6


def test_miss_rate_over_200_random_interruptions_over_echo():
    rng = np.random.default_rng(2027)
    misses, latencies, by_ratio = [], [], {}
    for k in range(200):
        ref, source, rig, mic, params = echo_trial(rng)
        onset = float(rng.uniform(0.7, 1.8))
        echo_rms = rms(mic)
        level = float(np.clip(max(echo_rms * rng.uniform(1.3, 4.0), 0.06), 0.06, 0.3))  # louder than the echo
        voice = trial_human(rng, ref.size / SR - onset, level, source)
        mic = mic.copy()
        start = int(onset * SR)
        mic[start : start + voice.size] += voice[: mic.size - start]
        at = detect(Rig(ref, jitter=0.04, arrival=rig.arrival, seed=k), mic)
        bucket = int(min(level / max(echo_rms, 1e-6), 4))
        by_ratio.setdefault(bucket, []).append(at is None)
        if at is None:
            misses.append((source, round(echo_rms, 3), round(level, 3)))
        else:
            latencies.append(at - onset)
    rate = len(misses) / 200
    print(
        f"misses: {len(misses)} of 200 interruptions over echo ({rate * 100:.1f} %); onset to cut off "
        f"{np.mean(latencies) * 1000:.0f} ms mean, {np.percentile(latencies, 90) * 1000:.0f} ms p90 (250 ms chunks)"
    )
    for bucket, flags in sorted(by_ratio.items()):
        print(f"  person at least {bucket}x the echo level: {sum(flags)} of {len(flags)} missed")
    assert rate < 0.10


def test_miss_rate_and_added_latency_without_echo():
    """A person talking while the reference plays, nothing of it in the microphone
    (headphones): the guard must not slow the interruption down."""
    rng = np.random.default_rng(2028)
    on, off, misses = [], [], 0
    for k in range(60):
        ref, source = trial_reference(rng, TRIAL_SECONDS)
        onset = float(rng.uniform(0.7, 1.8))
        mic = room_noise(ref.size, rng, float(rng.uniform(0.002, 0.01)))
        voice = trial_human(rng, ref.size / SR - onset, float(rng.uniform(0.05, 0.15)), source)
        start = int(onset * SR)
        mic[start : start + voice.size] += voice[: mic.size - start]
        a = detect(Rig(ref, jitter=0.04, seed=k), mic)
        b = detect(Rig(ref, jitter=0.04, seed=k), mic, guard=False)
        assert b is not None
        if a is None:
            misses += 1
        else:
            on.append(a - onset)
            off.append(b - onset)
    added = float(np.mean(on) - np.mean(off[: len(on)]))
    print(
        f"no echo: {misses} of 60 missed; onset to cut off {np.mean(on) * 1000:.0f} ms with the guard, "
        f"{np.mean(off) * 1000:.0f} ms without (added {added * 1000:.0f} ms)"
    )
    assert misses / 60 < 0.10
    assert added < 0.25


# ---------------------------------------------- the /ws/voice reply, real time


def _realtime_reply(server_module, monkeypatch, human):
    """The whole path of server._speak_streamed_reply with a scripted socket that
    delivers the microphone in real time (0.25 s per chunk). The fake player puts
    its sentences into the shared reference when they start playing, like tts._play."""
    import asyncio
    import threading

    import echo_guard
    from test_barge_in import FakeLlm, ScriptedSocket
    from test_barge_in import calibrated_vad as barge_vad

    rng = np.random.default_rng(31)
    sentence_audio = speech_segment("sample", 1.2, rng) * 0.15
    reference = echo_guard.shared_reference()
    reference.clear()
    state = {"t_start": None, "t_socket": None}

    class RealtimeTts:
        def __init__(self):
            self.cancel_seen = False

        def speak_stream(self, sentences, language="en", voice_style="female_natural", on_sentence_start=None, cancel=None):
            for sentence in sentences:
                if cancel is not None and cancel.is_set():
                    self.cancel_seen = True
                    return
                if on_sentence_start is not None:
                    on_sentence_start(sentence)
                now = time.monotonic()
                if state["t_start"] is None:
                    state["t_start"] = now
                reference.add(sentence_audio, SR, now)
                if cancel is not None and cancel.wait(1.2):
                    self.cancel_seen = True
                    reference.clear()
                    return

    class RealtimeSocket(ScriptedSocket):
        async def receive(self):
            await asyncio.sleep(0.25)
            if state["t_socket"] is None:
                state["t_socket"] = time.monotonic() - 0.25
            return await super().receive()

    tts_fake = RealtimeTts()
    llm = FakeLlm(sentences=4, gap_seconds=0.02)
    info = {}
    voice = speech_segment("question", 3.0, np.random.default_rng(5)) * 0.12

    def mic(index):
        out = room_noise(CHUNK, rng)
        if state["t_socket"] is None or state["t_start"] is None:
            return out
        chunk_start = state["t_socket"] + index * 0.25
        # what the speakers played, 120 ms of device latency, a second sentence copy after the first
        pos = (chunk_start - state["t_start"] - 0.12) * SR + np.arange(CHUNK)
        idx = np.floor(pos).astype(int)
        wrapped = idx % sentence_audio.size  # the fake player starts a sentence every 1.2 s, as long as the clip
        valid = (idx >= 0) & (wrapped < sentence_audio.size)
        echo = np.zeros(CHUNK, dtype=np.float32)
        echo[valid] = sentence_audio[wrapped[valid]]
        out = out + 0.5 * echo
        if human and chunk_start - state["t_start"] > 1.5:
            start = info.setdefault("onset_index", index)
            offset = (index - start) * CHUNK
            if offset + CHUNK <= voice.size:
                out = out + voice[offset : offset + CHUNK]
        return out.astype(np.float32)

    monkeypatch.setattr(server_module, "speak_stream", tts_fake.speak_stream)
    monkeypatch.setattr(server_module.orchestrator, "respond_stream", llm.respond_stream)
    socket = RealtimeSocket(mic)
    barge = {"vad": barge_vad(), "voice_holder": {"voice_style": "female_natural"}}

    async def go():
        return await server_module._speak_streamed_reply(socket, "tell me", "Guest", "en", "female_natural", None, barge)

    asyncio.run(asyncio.wait_for(go(), timeout=30))
    return socket, tts_fake, barge, info


def test_ws_voice_reply_its_own_echo_never_interrupts(server_module, monkeypatch):
    socket, tts_fake, barge, _info = _realtime_reply(server_module, monkeypatch, human=False)
    assert "interrupt" not in socket.types()
    assert socket.types()[-1] == "speaking_done"
    assert barge.get("seed") is None


def test_ws_voice_reply_a_person_talking_over_it_interrupts(server_module, monkeypatch):
    socket, tts_fake, barge, info = _realtime_reply(server_module, monkeypatch, human=True)
    assert "interrupt" in socket.types(), "the person must cut the reply off"
    assert tts_fake.cancel_seen
    latency_chunks = socket.index - info["onset_index"]
    print(f"ws/voice real time: cut off {latency_chunks} chunks ({latency_chunks * 250} ms of audio) after the person's first chunk")
    assert latency_chunks <= 4
