"""FastAPI real time backend for the voice interaction service.

Ties stt.py, vad.py, agent.py, and tts.py into one live conversational loop
over a WebSocket: audio in, noise robust voice activity detection,
streaming transcription, an orchestrated agent response (cloud multi agent
if reachable, local single agent otherwise), streaming speech out. This is
what turns the four standalone modules into an actual real time product
rather than four scripts someone runs one after another.

Protocol (WebSocket at /ws/voice):

    Client -> server:
        - Binary frames: raw float32 mono PCM audio chunks at
          vad.SAMPLE_RATE (16000 Hz), CHUNK_SECONDS long. The frontend
          captures microphone audio, resamples/converts it to this format,
          and sends each chunk as it is captured.
        - Text frames: small JSON control messages,
          {"type": "start_session", "language_hint": "en" | "si" | "ta" | null}
          sent once at the start of a session, before any audio, and
          {"type": "end_session"} to close cleanly.

    Server -> client (all JSON text frames):
        {"type": "status", "state": "calibrating" | "listening" | "thinking" | "speaking"}
        {"type": "partial_transcript", "text": "...", "language": "en"}
        {"type": "final_transcript", "text": "...", "language": "en"}
        {"type": "agent_response_chunk", "text": "..."}
            Sent repeatedly while the agent's reply is being generated,
            "text" holding the accumulated reply so far (not just the new
            piece), the same cumulative shape partial_transcript already
            uses. Lets the frontend show the reply appearing in real time
            during the "thinking" status instead of only once it is fully
            generated. See agent.py's Orchestrator.respond_stream(): only
            the local fallback path is actually streamed chunk by chunk;
            the cloud multi agent path yields its finished reply as one
            chunk (documented there, and in KNOWN_LIMITATIONS.md).
        {"type": "agent_response", "text": "...", "mode": "cloud multi agent (gemini)"
                                                          | "local single agent (Llama via Ollama)"}
            Sent once, after the last agent_response_chunk, with the
            complete text and which mode produced it.
        {"type": "speaking_done"}
        {"type": "error", "message": "..."}

    Audio playback itself happens server side in this pass (see
    ARCHITECTURE.md for why: streaming synthesized audio back over the
    socket for in-browser playback is a reasonable next step, but out of
    scope here, since this runs on one local machine for a live demo). The
    frontend reflects status and transcripts from these events; the actual
    voice comes out of the server machine's speakers via tts.speak_text().

Why transcription runs on a background thread per utterance: transcribe_stream()
(stt.py) is a plain synchronous generator, built that way so it can be unit
tested and driven from the standalone microphone test without any asyncio
involved. Its whisper calls block. Calling it directly inside this async
handler would stall the event loop (and every other connection) for the
duration of each transcription pass. Running it on a thread and bridging its
yielded events back into the event loop via an asyncio.Queue keeps audio
receiving and event sending fully concurrent with the blocking work, which
is what actually lets a partial transcript reach the client while the user
is still talking, not just after they stop.

Every stage logs its own latency (stt.py, agent.py, tts.py each already
print their own timings); this module additionally logs a per turn total so
real time performance has evidence behind it, not just a claim.
"""

import asyncio
import json
import queue
import threading
import time
from typing import Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from agent import Orchestrator
from stt import transcribe_stream
from tts import speak_text
from vad import SAMPLE_RATE, VoiceActivityDetector

app = FastAPI(title="Voice Interaction Backend")

# The frontend is a static page opened from a different origin/port during
# development (see frontend/), so CORS needs to be open for the demo. This
# is a deliberate scope boundary, not an oversight: locking this down is
# part of the authentication and production deployment work explicitly out
# of scope for this pass (see README.md).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Orchestrator()


async def _send_json(websocket: WebSocket, payload: dict):
    await websocket.send_text(json.dumps(payload))


class _ChunkQueueSource:
    """A synchronous iterator backed by a thread safe queue. The async
    websocket handler pushes audio chunks into it as they arrive; the
    background transcription thread pulls from it via plain blocking
    Queue.get(), which is exactly the iterator shape transcribe_stream()
    already expects."""

    _SENTINEL = object()

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()

    def put(self, chunk: np.ndarray):
        self._queue.put(chunk)

    def close(self):
        self._queue.put(self._SENTINEL)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._queue.get()
        if item is self._SENTINEL:
            raise StopIteration
        return item


async def _run_one_utterance(websocket: WebSocket, language_hint: Optional[str], vad: VoiceActivityDetector):
    """Streams audio chunks from the websocket into transcribe_stream()
    running on a background thread, forwarding each partial or final event
    to the client the moment it is produced. Returns
    (final_text, detected_language, session_ended)."""
    loop = asyncio.get_running_loop()
    chunk_source = _ChunkQueueSource()
    event_queue: "asyncio.Queue" = asyncio.Queue()
    worker_done = threading.Event()

    def _worker():
        try:
            for event in transcribe_stream(chunk_source, language_hint=language_hint, vad=vad):
                loop.call_soon_threadsafe(event_queue.put_nowait, event)
                if event.is_final:
                    break
        except Exception as exc:
            loop.call_soon_threadsafe(event_queue.put_nowait, exc)
        finally:
            worker_done.set()
            loop.call_soon_threadsafe(event_queue.put_nowait, None)

    threading.Thread(target=_worker, daemon=True).start()

    session_ended = False

    async def _receive_loop():
        nonlocal session_ended
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    session_ended = True
                    chunk_source.close()
                    return
                if message.get("bytes") is not None:
                    chunk_source.put(np.frombuffer(message["bytes"], dtype=np.float32))
                elif message.get("text") is not None:
                    try:
                        payload = json.loads(message["text"])
                    except ValueError:
                        continue
                    if payload.get("type") == "end_session":
                        session_ended = True
                        chunk_source.close()
                        return
        except WebSocketDisconnect:
            session_ended = True
            chunk_source.close()

    receive_task = asyncio.create_task(_receive_loop())

    final_text, final_language = None, None
    try:
        while True:
            item = await event_queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                await _send_json(websocket, {"type": "error", "message": f"Transcription error: {item}"})
                break
            if item.is_final:
                final_text, final_language = item.text, item.language
                if item.text:
                    await _send_json(
                        websocket, {"type": "final_transcript", "text": item.text, "language": item.language}
                    )
                chunk_source.close()
                break
            else:
                await _send_json(
                    websocket, {"type": "partial_transcript", "text": item.text, "language": item.language}
                )
    finally:
        chunk_source.close()  # idempotent-enough for our purposes; safe if already closed
        receive_task.cancel()
        try:
            await receive_task
        except (asyncio.CancelledError, Exception):
            pass

    return final_text, final_language, session_ended


async def _stream_agent_reply(text_input: str):
    """Runs orchestrator.respond_stream() (agent.py) on a background
    thread and yields each text chunk as it arrives, bridged into the
    event loop via an asyncio.Queue the same way _run_one_utterance above
    bridges transcribe_stream()'s blocking whisper calls. respond_stream()
    is itself a blocking generator (its requests.post call streams the
    HTTP response, but each iteration still blocks waiting on the next
    chunk from Ollama), so running it directly in this async handler would
    stall every other connection's event loop turn for the whole reply,
    exactly as an unstreamed call would."""
    loop = asyncio.get_running_loop()
    chunk_queue: "asyncio.Queue" = asyncio.Queue()

    def _worker():
        try:
            for chunk in orchestrator.respond_stream(text_input):
                loop.call_soon_threadsafe(chunk_queue.put_nowait, chunk)
        except Exception as exc:
            loop.call_soon_threadsafe(chunk_queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(chunk_queue.put_nowait, None)

    threading.Thread(target=_worker, daemon=True).start()

    while True:
        item = await chunk_queue.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()

    language_hint: Optional[str] = None
    try:
        start_message = await websocket.receive_text()
        payload = json.loads(start_message)
        if payload.get("type") == "start_session":
            language_hint = payload.get("language_hint") or None
    except Exception as exc:
        await _send_json(websocket, {"type": "error", "message": f"Invalid start message: {exc}"})
        await websocket.close()
        return

    vad = VoiceActivityDetector()
    await _send_json(websocket, {"type": "status", "state": "calibrating"})
    # The client sends about 1.5s of ambient audio (nobody speaking) right
    # after start_session, before the user is prompted to talk, so the
    # noise floor reflects the actual room rather than a fixed guess.
    calibration_samples = 0
    calibration_chunks = []
    try:
        while calibration_samples < int(1.5 * SAMPLE_RATE):
            message = await websocket.receive_bytes()
            chunk = np.frombuffer(message, dtype=np.float32)
            calibration_chunks.append(chunk)
            calibration_samples += chunk.size
        vad.calibrate_noise_floor(np.concatenate(calibration_chunks))
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await _send_json(websocket, {"type": "error", "message": f"Calibration failed: {exc}"})

    try:
        while True:
            turn_start = time.time()
            await _send_json(websocket, {"type": "status", "state": "listening"})

            final_text, detected_language, session_ended = await _run_one_utterance(websocket, language_hint, vad)

            if session_ended:
                return
            if not final_text:
                continue

            await _send_json(websocket, {"type": "status", "state": "thinking"})
            agent_start = time.time()
            reply_text = ""
            try:
                async for chunk in _stream_agent_reply(final_text):
                    reply_text += chunk
                    await _send_json(websocket, {"type": "agent_response_chunk", "text": reply_text})
            except Exception as exc:
                await _send_json(websocket, {"type": "error", "message": f"Agent error: {exc}"})
                continue
            agent_time = time.time() - agent_start

            await _send_json(
                websocket, {"type": "agent_response", "text": reply_text, "mode": orchestrator.last_mode}
            )

            await _send_json(websocket, {"type": "status", "state": "speaking"})
            tts_start = time.time()
            try:
                speak_text(reply_text, language=detected_language or "en", voice_style="female_natural")
            except Exception as exc:
                await _send_json(websocket, {"type": "error", "message": f"TTS error: {exc}"})
            tts_time = time.time() - tts_start

            await _send_json(websocket, {"type": "speaking_done"})

            total_time = time.time() - turn_start
            print(
                f"Turn latency: agent {agent_time:.2f}s, tts {tts_time:.2f}s, "
                f"total {total_time:.2f}s, mode={orchestrator.last_mode}"
            )
    except WebSocketDisconnect:
        return


class ChatRequest(BaseModel):
    text: str
    language: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    mode: str


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Text only alternative to the voice pipeline, for the frontend's
    Start Chat screen. Runs the same Orchestrator the voice flow uses (so
    the same cloud multi agent versus local fallback behavior applies),
    just without any STT or TTS involved. orchestrator.respond() is a
    blocking call (HTTP requests to Ollama or a cloud provider), so it
    runs on a thread via run_in_executor rather than directly in this
    async handler, for the same reason server.py's WebSocket transcription
    does: a blocking call here would stall every other connection's event
    loop turn until it finished."""
    loop = asyncio.get_running_loop()
    reply = await loop.run_in_executor(None, orchestrator.respond, request.text)
    return ChatResponse(reply=reply, mode=orchestrator.last_mode)


@app.get("/health")
async def health():
    """Plain status endpoint for a demo or a smoke test, reporting which
    provider paths are actually configured right now rather than what the
    code merely supports, so a quick check tells the real story."""
    return {
        "status": "ok",
        "cloud_llm_configured": config.cloud_llm_configured(),
        "cloud_llm_provider": config.CLOUD_LLM_PROVIDER,
        "cloud_speech_configured": config.cloud_speech_configured(),
        "cloud_speech_provider": config.CLOUD_SPEECH_PROVIDER,
        "supported_languages": config.SUPPORTED_LANGUAGES,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
