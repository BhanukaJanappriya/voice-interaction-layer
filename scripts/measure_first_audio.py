"""Measures the time to the first spoken word of a reply over /ws/voice.

A question is synthesized with edge-tts (a different voice from the
assistant's), streamed to the server in 0.5 s float32 chunks the way the
browser does, and the clock runs from the final_transcript event to the
status "speaking" event, which the server sends right as the first
sentence's audio starts. That is the same definition ARCHITECTURE.md uses
for "first audio after the final transcript".

Run with the app already started (python -m backend):

    python scripts/measure_first_audio.py --runs 8
    python scripts/measure_first_audio.py --runs 8 --text "How do I pay my bill?"
"""

import argparse
import asyncio
import io
import json
import statistics
import time

import edge_tts
import numpy as np
import soundfile as sf
import websockets

SAMPLE_RATE = 16000
CHUNK = SAMPLE_RATE // 2


async def synthesize_question(text: str, voice: str) -> np.ndarray:
    audio = bytearray()
    async for part in edge_tts.Communicate(text, voice).stream():
        if part["type"] == "audio":
            audio.extend(part["data"])
    samples, rate = sf.read(io.BytesIO(bytes(audio)), dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if rate != SAMPLE_RATE:
        positions = np.arange(0, len(samples), rate / SAMPLE_RATE)
        samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
    return samples


def chunks_of(samples: np.ndarray):
    for start in range(0, len(samples), CHUNK):
        piece = samples[start : start + CHUNK]
        if len(piece) < CHUNK:
            piece = np.pad(piece, (0, CHUNK - len(piece)))
        yield piece.astype(np.float32)


async def one_run(url: str, question: np.ndarray, language: str, extra_start: dict):
    async with websockets.connect(url, max_size=None) as socket:
        start = {"type": "start_session", "language_hint": language, "user_name": None, "greet": False}
        start.update(extra_start)
        await socket.send(json.dumps(start))
        quiet = np.zeros(CHUNK, dtype=np.float32)
        final_at = first_audio_at = None
        pending = list(chunks_of(question)) + [quiet] * 6  # trailing silence ends the utterance
        calibration_left = 2
        state = ""
        while True:
            # Send one chunk each half second while the server is listening.
            if state in ("", "calibrating") and calibration_left > 0:
                await socket.send(quiet.tobytes())
                calibration_left -= 1
            elif state == "listening" and pending:
                await socket.send(pending.pop(0).tobytes())
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=0.5 if state == "listening" else 30)
            except asyncio.TimeoutError:
                continue
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "status":
                state = message["state"]
                if state == "speaking" and final_at is not None and first_audio_at is None:
                    first_audio_at = time.perf_counter()
            elif kind == "final_transcript" and final_at is None:
                final_at = time.perf_counter()
            elif kind == "speaking_done" and first_audio_at is not None:
                break
            elif kind == "error":
                # The machine running a test may have no speakers: playback then
                # fails after the "speaking" status, which has already been sent.
                if first_audio_at is None:
                    raise RuntimeError(message.get("message"))
                print(f"  (server reported: {message.get('message')})")
        await socket.send(json.dumps({"type": "end_session"}))
        return first_audio_at - final_at


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/voice")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--text", default="What is the hotline number of SLT Mobitel?")
    parser.add_argument("--language", default="en")
    parser.add_argument("--voice", default="en-GB-RyanNeural")
    parser.add_argument("--start-extra", default="{}", help="JSON merged into start_session")
    args = parser.parse_args()

    question = await synthesize_question(args.text, args.voice)
    extra = json.loads(args.start_extra)
    results = []
    for index in range(args.runs):
        seconds = await one_run(args.url, question, args.language, extra)
        results.append(seconds)
        print(f"run {index + 1}: first audio {seconds:.2f} s after the final transcript")
    print(f"median {statistics.median(results):.2f} s, min {min(results):.2f} s, max {max(results):.2f} s")


if __name__ == "__main__":
    asyncio.run(main())
