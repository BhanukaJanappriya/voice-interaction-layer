"""HTTP and WebSocket text to speech API for voicekit.

    POST /api/tts/stream   {text, language, voice, format}  chunked body
    WS   /ws/tts           {"type":"speak",...} / {"type":"cancel"}

create_router() returns an APIRouter; the host decides where to mount it and
supplies the API keys through a callable, so keys can be reloaded without a
restart. Security choices, each deliberate:

  * No keys configured means every request is refused (fail closed).
  * Keys are compared with hmac.compare_digest against every configured key,
    so timing does not reveal how much of a key matched.
  * Each key has a sliding 60 second window of allowed requests (the key
    itself is never stored, only a SHA-256 digest of it); callers with bad
    keys are limited per client address so keys cannot be guessed quickly.
  * Text longer than settings.max_text_chars is refused with 422 before any
    synthesis starts.
  * Neither keys nor text are ever logged. The only output is latency lines.

Body formats: pcm (raw 16 bit mono), wav (streaming WAV, unknown length) and
ndjson, JSON lines {"type": "audio"|"timing"|"sentence"|"done"} with base64
PCM. The sample rate is in the X-Sample-Rate response header.
"""

import asyncio
import hashlib
import hmac
import threading
import time
from collections import deque
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .audio import resample_pcm16, wav_header
from .core import synthesize_stream
from .settings import VoicekitSettings
from .sinks import UtteranceClock, WsTranslator, events_to_ndjson
from .types import AudioChunk, InvalidRequest, SynthesisDone

WINDOW_SECONDS = 60.0
FAILED_AUTH_PER_WINDOW = 20
MAX_CONCURRENT = 8


class TtsRequest(BaseModel):
    text: str
    language: str = "en"
    voice: str = "female_natural"
    format: str = "ndjson"


class SlidingWindow:
    """Allows at most `limit` hits per identity in the last window seconds."""

    def __init__(self, limit: int, window: float = WINDOW_SECONDS):
        self._limit = limit
        self._window = window
        self._hits: dict = {}
        self._lock = threading.Lock()

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(identity, deque())
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory against many fake identities
                for key in [k for k, v in self._hits.items() if not v or now - v[-1] > self._window]:
                    del self._hits[key]
            return True


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _match_key(presented: Optional[str], keys: List[str]) -> Optional[str]:
    """Returns the identity (digest) of the matching key, or None. Compares
    against every key so the loop time does not depend on which one matched."""
    if not presented or not keys:
        return None
    found = None
    probe = presented.encode("utf-8")
    for key in keys:
        if hmac.compare_digest(probe, key.encode("utf-8")):
            found = _digest(key)
    return found


def create_router(
    settings: VoicekitSettings,
    api_keys: Callable[[], List[str]],
    rate_limit_per_minute: int = 30,
) -> APIRouter:
    router = APIRouter()
    key_limiter = SlidingWindow(max(1, rate_limit_per_minute))
    failure_limiter = SlidingWindow(FAILED_AUTH_PER_WINDOW)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    def authenticate(presented: Optional[str], client: str) -> str:
        """Returns the key identity or raises HTTPException (401 or 429)."""
        identity = _match_key(presented, list(api_keys() or []))
        if identity is None:
            if not failure_limiter.allow(client):
                raise HTTPException(status_code=429, detail="too many failed attempts")
            raise HTTPException(status_code=401, detail="missing or invalid API key")
        if not key_limiter.allow(identity):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        return identity

    def check_text(req: TtsRequest) -> None:
        if not req.text.strip():
            raise HTTPException(status_code=422, detail="text is empty")
        if len(req.text) > settings.max_text_chars:
            raise HTTPException(status_code=422, detail=f"text is longer than {settings.max_text_chars} characters")
        if req.format not in ("pcm", "wav", "ndjson"):
            raise HTTPException(status_code=422, detail="format must be pcm, wav or ndjson")

    @router.post("/api/tts/stream")
    def tts_stream(req: TtsRequest, request: Request):
        client = request.client.host if request.client else "unknown"
        authenticate(request.headers.get("x-api-key"), client)
        check_text(req)
        try:
            events = synthesize_stream(req.text, req.language, req.voice, settings)
        except InvalidRequest as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not slots.acquire(blocking=False):
            events.cancel()
            raise HTTPException(status_code=429, detail="too many concurrent syntheses")

        clock = UtteranceClock()
        buffered: list = []  # events seen before the first audio, replayed after
        first_rate = 0
        try:
            for event in events:
                buffered.append(event)
                if isinstance(event, AudioChunk):
                    first_rate = event.sample_rate
                    break
                if isinstance(event, SynthesisDone):
                    break
        except Exception:
            events.cancel()
            slots.release()
            raise HTTPException(status_code=500, detail="synthesis failed")
        if not first_rate:
            events.cancel()
            slots.release()
            return JSONResponse(status_code=503, content={"detail": "no voice provider could speak this text"})

        def replay():
            for event in buffered:
                yield event
            yield from events

        def body():
            try:
                if req.format == "ndjson":
                    yield from events_to_ndjson(replay(), clock)
                    return
                if req.format == "wav":
                    yield wav_header(first_rate)
                for event in replay():
                    if isinstance(event, AudioChunk):
                        yield resample_pcm16(event.pcm, event.sample_rate, first_rate)
            finally:
                events.cancel()  # runs when the client disconnects too
                slots.release()

        media = {"pcm": "application/octet-stream", "wav": "audio/wav", "ndjson": "application/x-ndjson"}[req.format]
        return StreamingResponse(
            body(), media_type=media, headers={"X-Sample-Rate": str(first_rate), "Cache-Control": "no-store"}
        )

    @router.websocket("/ws/tts")
    async def tts_socket(ws: WebSocket):
        client = ws.client.host if ws.client else "unknown"
        presented = ws.headers.get("x-api-key") or ws.query_params.get("api_key")
        try:
            authenticate(presented, client)
        except HTTPException as exc:
            await ws.close(code=1008, reason=str(exc.detail))
            return
        await ws.accept()
        identity = _match_key(presented, list(api_keys() or []))
        loop = asyncio.get_running_loop()
        current: dict = {"task": None, "events": None}

        async def send_error(message: str) -> None:
            await ws.send_json({"type": "error", "message": message})

        async def run_speak(msg: dict, events) -> None:
            """Pulls events from the synthesis thread one at a time (so the
            event loop never blocks) and sends them as they arrive."""
            translator = WsTranslator()
            iterator = iter(events)
            sentinel = object()
            try:
                while True:
                    event = await loop.run_in_executor(None, next, iterator, sentinel)
                    if event is sentinel:
                        break
                    for kind, payload in translator.feed(event):
                        if kind == "json":
                            await ws.send_json(payload)
                        else:
                            await ws.send_bytes(payload)
            except InvalidRequest as exc:
                await send_error(str(exc))
            except (WebSocketDisconnect, RuntimeError):
                pass  # client went away mid stream
            finally:
                events.cancel()
                slots.release()

        async def start(msg: dict) -> None:
            try:
                req = TtsRequest(text=str(msg.get("text", "")), language=str(msg.get("language", "en")),
                                 voice=str(msg.get("voice", "female_natural")))
                check_text(req)
                events = synthesize_stream(req.text, req.language, req.voice, settings)
            except HTTPException as exc:
                await send_error(str(exc.detail))
                return
            except InvalidRequest as exc:
                await send_error(str(exc))
                return
            if not slots.acquire(blocking=False):
                events.cancel()
                await send_error("too many concurrent syntheses")
                return
            current["events"] = events
            current["task"] = asyncio.ensure_future(run_speak(msg, events))

        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type") if isinstance(msg, dict) else None
                busy = current["task"] is not None and not current["task"].done()
                if kind == "cancel":
                    if current["events"] is not None:
                        current["events"].cancel()
                    if current["task"] is not None:
                        await asyncio.gather(current["task"], return_exceptions=True)
                    await ws.send_json({"type": "cancelled"})
                elif kind == "speak":
                    if busy:
                        await send_error("a synthesis is already running, send cancel first")
                    elif not key_limiter.allow(identity or "?"):
                        await send_error("rate limit exceeded")
                    else:
                        await start(msg)
                else:
                    await send_error("unknown message type")
        except WebSocketDisconnect:
            pass
        except Exception:
            pass  # bad JSON or a broken socket: just end the session
        finally:
            if current["events"] is not None:
                current["events"].cancel()

    return router
