"""Repeats the first audio byte measurement several times per voice and prints the median and
the range, because a single run per sentence (edge_samples.py) is dominated by network noise.
Usage: venv\\Scripts\\python tools\\voice_eval\\latency_repeat.py [runs]   (default 5, interleaved voices)
"""
import asyncio
import json
import pathlib
import statistics
import sys
import time

import edge_tts

HERE = pathlib.Path(__file__).parent
VOICES = [
    "en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural", "en-US-AriaNeural", "en-US-GuyNeural",
    "si-LK-ThiliniNeural", "si-LK-SameeraNeural", "ta-LK-SaranyaNeural", "ta-LK-KumarNeural",
]


async def first_byte(voice, text):
    start = time.perf_counter()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            return time.perf_counter() - start
    return None


async def main(runs):
    sentences = json.load(open(HERE / "sentences.json", encoding="utf-8"))
    results = {v: [] for v in VOICES}
    for r in range(runs):
        for v in VOICES:
            text = sentences[v[:2]][1]
            try:
                t = await first_byte(v, text)
                if t is not None:
                    results[v].append(t)
            except Exception as exc:
                print("ERR", v, type(exc).__name__)
    for v, ts in results.items():
        if ts:
            print(f"{v:34s} n={len(ts)} median {statistics.median(ts):.2f}s min {min(ts):.2f} max {max(ts):.2f}")


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
