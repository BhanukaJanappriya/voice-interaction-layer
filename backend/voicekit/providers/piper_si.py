"""Optional offline Sinhala female voice: Piper si_LK-dilu-medium. UNAVAILABLE by default.

Finding (2026-10-15): the repository https://huggingface.co/rhasspy/piper-voices has NO Sinhala
directory (its tree API answers "si does not exist on main", and the tag list has no si), so the
model named in the brief could not be found there and nothing was downloaded. This adapter
therefore only works if someone places a Piper Sinhala model by hand:

    data/models/piper_si/si_LK-dilu-medium.onnx
    data/models/piper_si/si_LK-dilu-medium.onnx.json
    data/models/piper_si/si_LK-dilu-medium.onnx.LICENSE   (first line: the licence you verified)

(data/ is already in .gitignore.) Set PIPER_SI_MODEL_DIR to use another folder. Without the
files, onnxruntime or the espeak-ng program on PATH, available() is False and stream() raises
ProviderUnavailable. Female only: a male request is refused, there is no Sinhala male model.
Inference code is shared with local_model.py and equally never run end to end here.
"""

import os
import shutil
import threading
from pathlib import Path
from typing import Iterator, Optional

from ..types import ProviderUnavailable
from . import local_model
from .base import chunked, pcm16_from_float

NAME = "piper_si"
MODEL_NAME = "si_LK-dilu-medium.onnx"
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "data" / "models" / "piper_si"


def model_dir(settings=None) -> Path:
    return Path(getattr(settings, "piper_si_model_dir", "") or os.environ.get("PIPER_SI_MODEL_DIR", "") or DEFAULT_DIR)


def model_file(settings=None) -> Optional[Path]:
    path = model_dir(settings) / MODEL_NAME
    return path if path.is_file() and Path(str(path) + ".json").is_file() else None


def licence(settings=None) -> str:
    path = model_file(settings)
    return local_model.licence_of(path) if path else "no model installed"


def available(settings=None) -> bool:
    if model_file(settings) is None:
        return False
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return shutil.which("espeak-ng") is not None


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    if language != "si":
        raise ProviderUnavailable("piper_si speaks Sinhala only")
    if voice == "male":
        raise ProviderUnavailable("no Sinhala male model")
    path = model_file(settings)
    if path is None or not available(settings):
        raise ProviderUnavailable("Piper Sinhala model, onnxruntime or espeak-ng not installed")
    try:
        import numpy as np

        session, config = local_model._load(path)
        phonemes = local_model._phonemize(text, config.get("espeak", {}).get("voice", "si"))
        ids = local_model._phoneme_ids(phonemes, config["phoneme_id_map"])
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
        raise ProviderUnavailable(f"piper_si failed: {type(exc).__name__}") from exc
    for chunk in chunked(pcm, rate):
        if cancel.is_set():
            return
        yield chunk
