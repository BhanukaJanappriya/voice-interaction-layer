"""Synthesizes the fixed sentence set with every edge-tts candidate and prints
time to first audio byte. Samples go to tools/voice_eval/samples (not committed)."""
import asyncio, json, pathlib, time
import edge_tts

HERE = pathlib.Path(__file__).parent
CANDIDATES = {
    "en": ["en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural", "en-US-AriaNeural", "en-US-GuyNeural"],
    "si": ["si-LK-ThiliniNeural", "si-LK-SameeraNeural"],
    "ta": ["ta-LK-SaranyaNeural", "ta-LK-KumarNeural", "ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"],
}


async def one(voice, text, path):
    start = time.perf_counter()
    first = None
    with open(path, "wb") as out:
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                if first is None:
                    first = time.perf_counter() - start
                out.write(chunk["data"])
    return first, time.perf_counter() - start


async def main():
    sentences = json.load(open(HERE / "sentences.json", encoding="utf-8"))
    for lang, voices in CANDIDATES.items():
        for voice in voices:
            firsts = []
            for i, text in enumerate(sentences[lang]):
                try:
                    f, total = await one(voice, text, HERE / "samples" / f"{voice}_{i}.mp3")
                    firsts.append(round(f, 2))
                except Exception as exc:
                    firsts.append(f"ERR {type(exc).__name__}")
            print(lang, voice, firsts)

asyncio.run(main())
