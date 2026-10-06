"""Local open model provider: Piper voices run with onnxruntime, offline.

NOT VERIFIED END TO END: no Piper model files and no espeak-ng were present
on the development machine, so this code is written from the Piper model
format but has never produced audio here. It is switched off by default in
the only way that matters: it does nothing unless model files exist.

How to enable it (see docs/VOICEKIT.md):
  1. Put a Piper voice in settings.local_voice_dir as
     <lang>_female.onnx or <lang>_male.onnx plus the same name with .json
     added, for example en_female.onnx, en_female.onnx.json, en_male.onnx,
     en_male.onnx.json.
  2. Install the espeak-ng program and put it on PATH. Piper models consume
     espeak IPA phonemes; the only maintained phonemizer for that is native
     code, so this provider calls the espeak-ng command line instead of a
     Python wheel that needs compiling.
  3. Check the licence of the voice you chose. Piper VOICES carry the licence
     of the dataset they were trained on, which varies from public domain to
     non commercial. Put the licence name in <file>.onnx.LICENSE next to the
     model; list_voices() reports it. Without that file it reports "see
     docs/THIRD_PARTY.md" and the model should not be used commercially until
     someone has checked it.

Kokoro (Apache-2.0 model) needs the same kind of phonemizer plus a different
input format and is not implemented.

Output is 16 bit mono PCM at the model sample rate (usually 22050 Hz), made
per sentence and then chunked, so it is not streamed inside one sentence.
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterator, Optional

from ..types import ProviderUnavailable
from .base import chunked, pcm16_from_float

NAME = "local_model"
_sessions: dict = {}
_STYLE_FILE = {"female_natural": "female", "male": "male"}


def model_path(settings, language: str, voice: str) -> Optional[Path]:
    if not settings.local_voice_dir:
        return None
    path = Path(settings.local_voice_dir) / f"{language}_{_STYLE_FILE.get(voice, 'female')}.onnx"
    if path.is_file() and Path(str(path) + ".json").is_file():
        return path
    return None


def licence_of(path: Path) -> str:
    note = Path(str(path) + ".LICENSE")
    if note.is_file():
        first = note.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if first:
            return first[0][:120]
    return "see docs/THIRD_PARTY.md"


def available(settings) -> bool:
    if not settings.local_voice_dir or not Path(settings.local_voice_dir).is_dir():
        return False
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return shutil.which("espeak-ng") is not None


def _load(path: Path):
    key = str(path)
    if key not in _sessions:
        import onnxruntime as ort

        config = json.loads(Path(key + ".json").read_text(encoding="utf-8"))
        _sessions[key] = (ort.InferenceSession(key, providers=["CPUExecutionProvider"]), config)
    return _sessions[key]


def _phonemize(text: str, espeak_voice: str) -> str:
    out = subprocess.run(
        ["espeak-ng", "-q", "--ipa", "-v", espeak_voice, text],
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
    )
    return " ".join(out.stdout.split())


def _phoneme_ids(phonemes: str, id_map: dict) -> list:
    """Piper layout: BOS, then each phoneme followed by a pad id, then EOS.
    Characters the model does not know are skipped."""
    ids = list(id_map.get("^", [0]))
    for ch in phonemes:
        if ch in id_map:
            ids.extend(id_map[ch])
            ids.extend(id_map.get("_", [0]))
    ids.extend(id_map.get("$", [0]))
    return ids


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    path = model_path(settings, language, voice)
    if path is None or not available(settings):
        raise ProviderUnavailable("no local model files, onnxruntime or espeak-ng")
    try:
        import numpy as np

        session, config = _load(path)
        phonemes = _phonemize(text, config.get("espeak", {}).get("voice", "en-us"))
        ids = _phoneme_ids(phonemes, config["phoneme_id_map"])
        inference = config.get("inference", {})
        feeds = {
            "input": np.array([ids], dtype=np.int64),
            "input_lengths": np.array([len(ids)], dtype=np.int64),
            "scales": np.array(
                [inference.get("noise_scale", 0.667), inference.get("length_scale", 1.0), inference.get("noise_w", 0.8)],
                dtype=np.float32,
            ),
        }
        if "sid" in {i.name for i in session.get_inputs()}:
            feeds["sid"] = np.array([0], dtype=np.int64)
        audio = session.run(None, feeds)[0].squeeze()
        peak = max(0.01, float(np.abs(audio).max()))
        pcm = pcm16_from_float(audio / max(1.0, peak))
        rate = int(config.get("audio", {}).get("sample_rate", 22050))
    except Exception as exc:
        raise ProviderUnavailable(f"local model failed: {type(exc).__name__}") from exc
    for chunk in chunked(pcm, rate):
        if cancel.is_set():
            return
        yield chunk
