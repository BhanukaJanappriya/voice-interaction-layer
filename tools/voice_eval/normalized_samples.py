"""Before and after samples for the text normalizer, for the owner to listen to.

For every sentence in sentences.json and one voice per language and gender (the first
local voice in config/voices.yaml) this writes two mp3 files, <voice>_<i>_raw.mp3 (the
sentence as written) and <voice>_<i>_norm.mp3 (after backend/voice_text.normalize), into
tools/voice_eval/samples/normalized/, plus before_after.txt with the texts. Needs internet
(edge-tts). Samples are not committed. Run:  venv\\Scripts\\python tools\\voice_eval\\normalized_samples.py
"""
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))

import edge_tts  # noqa: E402
from voice_text import normalize  # noqa: E402
from voicekit.voice_config import lookup  # noqa: E402

OUT = HERE / "samples" / "normalized"


async def synth(voice, text, path):
    try:
        with open(path, "wb") as fh:
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
        return True
    except Exception as exc:
        print("ERR", voice, type(exc).__name__)
        return False


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sentences = json.load(open(HERE / "sentences.json", encoding="utf-8"))
    lines = []
    for lang, items in sentences.items():
        for gender in ("female", "male"):
            voice = lookup("local", lang, gender)[0]
            for i, text in enumerate(items):
                spoken = normalize(text, lang)
                lines.append(f"[{lang} {voice} #{i}]\nBEFORE: {text}\nAFTER : {spoken}\n")
                await synth(voice, text, OUT / f"{voice}_{i}_raw.mp3")
                await synth(voice, spoken, OUT / f"{voice}_{i}_norm.mp3")
    (OUT / "before_after.txt").write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT)


asyncio.run(main())
