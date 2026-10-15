"""GeminiLiveSession: one native speech to speech conversation on the Gemini Live API.

    session = GeminiLiveSession(tool_executor)       # tool_executor: async (name, args) -> dict
    ok = await session.open(cfg)                     # False when the connect failed (a Failed event is queued)
    await session.send_audio(pcm16_16k)              # microphone audio, any chunk size
    async for event in session.events: ...           # see the event classes below
    await session.close()

Events (all plain dataclasses):
    AudioOut(pcm24k)            model speech, PCM16 mono 24 kHz
    InputText(text, final)      what the caller said, CUMULATIVE for the utterance (the
                                partial_transcript / final_transcript shape of /ws/voice)
    OutputText(text, final)     what the model said, CUMULATIVE for the turn (agent_response_chunk / agent_response)
    Interrupted                 barge in: queued AudioOut was dropped, stop playback now
    TurnComplete                the model finished its turn
    ToolActivity(name)          a tool call started (the page can show a status line)
    GoAway(seconds)             the server will close the connection soon
    Resumed(handle)             the connection was replaced and the conversation continued
    Failed(reason_class, detail)  a GENUINE failure (errors.GENUINE); the stream ends after it

A safety block, a refusal, a normal close and close() are NOT failures: the stream just ends and
`closed_reason` says why. Failed is only emitted for the classes in errors.GENUINE.

The Live API does the voice activity detection and barge in itself (automatic activity
detection is on); the model's language is only in the system instruction. A long conversation
survives the 15 minute audio cap (sliding window compression) and the roughly 10 minute
connection life (resumption handle, reconnect at the next gap after GoAway).

Logs carry event names and timings only: never keys, handles, audio or text.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Deque, Dict, Optional

from google.genai import types

from . import errors
from .config import LiveSessionConfig
from .resume import AudioBuffer, GoAwayPlan, ResumptionStore

log = logging.getLogger("gemini_live")


# Events


@dataclass
class AudioOut:
    pcm24k: bytes = field(repr=False)


@dataclass
class InputText:
    text: str = field(repr=False)
    final: bool = False


@dataclass
class OutputText:
    text: str = field(repr=False)
    final: bool = False


@dataclass
class Interrupted:
    pass


@dataclass
class TurnComplete:
    pass


@dataclass
class ToolActivity:
    name: str = ""


@dataclass
class GoAway:
    seconds: float = 0.0


@dataclass
class Resumed:
    handle: str = field(default="", repr=False)


@dataclass
class Failed:
    reason_class: str = ""
    detail: str = ""


LiveEvent = Any
ToolExecutorFn = Callable[[str, dict], Awaitable[dict]]


class _EventBuffer:
    """Unbounded event queue whose queued AudioOut can be dropped (barge in)."""

    def __init__(self) -> None:
        self._items: Deque[Any] = deque()
        self._wake = asyncio.Event()
        self._closed = False

    def put(self, event: Any) -> None:
        if not self._closed:
            self._items.append(event)
            self._wake.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def drop_audio(self) -> int:
        before = len(self._items)
        self._items = deque(e for e in self._items if not isinstance(e, AudioOut))
        return before - len(self._items)

    async def get(self) -> Any:
        while True:
            if self._items:
                return self._items.popleft()
            if self._closed:
                return None
            self._wake.clear()
            await self._wake.wait()


def parse_seconds(value: Any) -> float:
    """GoAway.time_left arrives as '57s' (a protobuf duration string)."""
    if value is None:
        return 0.0
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    text = str(value).strip().lower().rstrip("s")
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def default_client_factory(cfg: LiveSessionConfig):
    from google import genai

    return genai.Client(api_key=cfg.api_key)


class GeminiLiveSession:
    def __init__(
        self,
        tool_executor: Optional[ToolExecutorFn] = None,
        client_factory: Optional[Callable[[LiveSessionConfig], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.tool_executor = tool_executor
        self._client_factory = client_factory or default_client_factory
        self._clock = clock
        self._cfg: Optional[LiveSessionConfig] = None
        self._events = _EventBuffer()
        self._task: Optional[asyncio.Task] = None
        self._ready: Optional["asyncio.Future[bool]"] = None
        self._session: Any = None
        self._connected = asyncio.Event()
        self._closing = False
        self._store = ResumptionStore()
        self._goaway = GoAwayPlan()
        self._buffer = AudioBuffer()
        self._tools: Dict[str, asyncio.Task] = {}
        self._failures = errors.FailureWindow()
        self._in_turn = False
        self._input_text = ""
        self._output_text = ""
        self._input_open = False
        self._output_open = False
        self.closed_reason: Optional[str] = None
        self.reconnects = 0
        self.connect_ms: Optional[int] = None

    # Public API

    @property
    def events(self) -> AsyncIterator[LiveEvent]:
        return self._iterate()

    @property
    def resumption_handle(self) -> Optional[str]:
        """The latest handle, for a caller that wants to start a new session with it."""
        return self._store.handle

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def open(self, cfg: LiveSessionConfig) -> bool:
        """Connects and waits for the setup to finish. Never raises. False means
        a Failed event is queued (and `closed_reason` is set)."""
        self._cfg = cfg
        self._store = ResumptionStore(cfg.resumption_handle)
        self._buffer = AudioBuffer(cfg.reconnect_buffer_seconds)
        self._goaway = GoAwayPlan(cfg.goaway_margin_seconds, self._clock)
        if not cfg.api_key and self._client_factory is default_client_factory:
            self._fail(errors.Outcome(errors.MISSING_KEY, "no api key configured"))
            return False
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run(), name="gemini-live")
        try:
            return bool(await asyncio.wait_for(asyncio.shield(self._ready), cfg.setup_timeout_seconds + 2.0))
        except asyncio.TimeoutError:
            self._fail(errors.Outcome(errors.TIMEOUT, "setup timed out"))
            await self._stop_task()
            return False

    async def send_audio(self, pcm16_16k: bytes) -> None:
        """Microphone audio, PCM16 mono 16 kHz. While the connection is being
        replaced the newest second is kept and sent afterwards."""
        if self._closing or not pcm16_16k or self.closed_reason:
            return
        session = self._session
        if session is None or not self._connected.is_set():
            self._buffer.add(pcm16_16k)
            return
        try:
            await session.send_realtime_input(audio=types.Blob(data=pcm16_16k, mime_type="audio/pcm;rate=16000"))
        except Exception:  # noqa: BLE001  the receive loop sees the close and reconnects
            self._buffer.add(pcm16_16k)

    async def send_text(self, text: str) -> None:
        """A text turn for the model (for example: the customer pressed Confirm, the hub said X).
        Dropped when the connection is down."""
        session = self._session
        if session is not None and self._connected.is_set() and text:
            try:
                await session.send_realtime_input(text=text)
            except Exception:  # noqa: BLE001
                pass

    async def end_audio(self) -> None:
        """The microphone was switched off (lets the server flush a cached utterance)."""
        session = self._session
        if session is not None and self._connected.is_set():
            try:
                await session.send_realtime_input(audio_stream_end=True)
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        self._closing = True
        if self.closed_reason is None:
            self.closed_reason = errors.USER_CLOSED
        await self._stop_task()
        self._events.close()

    # Internals

    async def _iterate(self) -> AsyncIterator[LiveEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def _stop_task(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for tool in list(self._tools.values()):
            tool.cancel()
        self._tools.clear()
        self._connected.clear()

    def _fail(self, outcome: errors.Outcome) -> None:
        """A genuine failure: queue Failed, end the stream, release open()."""
        if self.closed_reason is None:
            self.closed_reason = outcome.reason_class
        log.info("gemini_live failed reason=%s", outcome.reason_class)
        self._events.put(Failed(outcome.reason_class, outcome.detail))
        self._events.close()
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(False)

    def _end(self, reason: str) -> None:
        """A normal end (not a failure)."""
        if self.closed_reason is None:
            self.closed_reason = reason
        log.info("gemini_live ended reason=%s", reason)
        self._events.close()
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(False)

    def build_live_config(self) -> types.LiveConnectConfig:
        cfg = self._cfg
        assert cfg is not None
        detection = types.AutomaticActivityDetection(disabled=False)
        if cfg.end_silence_ms:
            detection = types.AutomaticActivityDetection(disabled=False, silence_duration_ms=int(cfg.end_silence_ms))
        handle = self._store.handle
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=cfg.system_instruction or None,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=cfg.voice_name))
            ),
            tools=list(cfg.tools) or None,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(handle=handle),
            context_window_compression=(
                types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()) if cfg.sliding_window else None
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=detection,
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
            ),
        )

    async def _connect(self):
        cfg = self._cfg
        assert cfg is not None
        client = self._client_factory(cfg)
        manager = client.aio.live.connect(model=cfg.model, config=self.build_live_config())
        session = await asyncio.wait_for(manager.__aenter__(), cfg.setup_timeout_seconds)
        return manager, session

    async def _run(self) -> None:
        cfg = self._cfg
        assert cfg is not None
        first = True
        misses = 0
        try:
            while not self._closing:
                started = self._clock()
                try:
                    manager, session = await self._connect()
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    outcome = errors.classify(exc)
                    if first or not outcome.genuine or outcome.reason_class in (
                        errors.BILLING_OR_QUOTA, errors.INVALID_KEY, errors.MODEL_UNAVAILABLE, errors.MISSING_KEY, errors.BAD_REQUEST
                    ):
                        self._fail(outcome) if outcome.genuine else self._end(outcome.reason_class)
                        return
                    misses += 1
                    if misses > cfg.max_reconnects or self._failures.record():
                        self._fail(errors.Outcome(errors.REPEATED_FAILURE, outcome.reason_class))
                        return
                    await asyncio.sleep(min(0.2 * misses, 1.0))
                    continue

                ms = int((self._clock() - started) * 1000)
                log.info("gemini_live connected ms=%d reconnect=%s", ms, not first)
                self._session = session
                if first:
                    self.connect_ms = ms
                else:
                    self.reconnects += 1
                    if self._store.handle:
                        self._events.put(Resumed(self._store.handle))
                misses = 0
                self._goaway.clear()
                self._reset_turn()
                try:
                    await self._flush_buffer(session)
                    self._connected.set()
                    if first and self._ready is not None and not self._ready.done():
                        self._ready.set_result(True)
                    first = False
                    verdict = await self._receive_loop(session)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    verdict = ("error", errors.classify(exc))
                finally:
                    self._connected.clear()
                    self._session = None
                    for tool in list(self._tools.values()):
                        tool.cancel()
                    self._tools.clear()
                    try:
                        await manager.__aexit__(None, None, None)
                    except BaseException:  # noqa: BLE001
                        pass

                kind = verdict[0]
                if kind == "reconnect":
                    continue
                if kind == "ended":
                    self._end(errors.USER_CLOSED)
                    return
                outcome = verdict[1]
                if self._closing:
                    return
                if not outcome.genuine:
                    if outcome.reason_class == errors.USER_CLOSED and self._goaway.armed:
                        continue
                    self._end(outcome.reason_class)
                    return
                # A dropped connection: continue the conversation only when there is a handle.
                if outcome.reason_class in (errors.OUTAGE, errors.TIMEOUT) and self._store.handle:
                    if self._failures.record():
                        self._fail(errors.Outcome(errors.REPEATED_FAILURE, outcome.reason_class))
                        return
                    continue
                self._fail(outcome)
                return
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001  nothing may leave this task unreported
            self._fail(errors.classify(exc))

    async def _flush_buffer(self, session) -> None:
        while len(self._buffer):
            data = self._buffer.drain()
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

    async def _receive_loop(self, session):
        """Returns ("reconnect",) or ("ended",); a closed socket raises."""
        idle = 0
        while not self._closing:
            generator = session.receive()
            got = False
            try:
                while True:
                    left = self._goaway.remaining()
                    if left is None:
                        message = await generator.__anext__()
                    else:
                        if left <= 0:
                            return ("reconnect",)
                        try:
                            message = await asyncio.wait_for(generator.__anext__(), left)
                        except asyncio.TimeoutError:
                            if self._goaway.remaining() == 0:
                                return ("reconnect",)
                            raise
                    got = True
                    if await self._handle(session, message) == "reconnect":
                        return ("reconnect",)
            except StopAsyncIteration:
                pass
            finally:
                try:
                    await generator.aclose()
                except BaseException:  # noqa: BLE001
                    pass
            idle = 0 if got else idle + 1
            if idle >= 3:  # the SDK ended receive() three times with nothing: the stream is over
                return ("ended",)
            await asyncio.sleep(0)
        return ("ended",)

    # Messages

    def _reset_turn(self) -> None:
        self._in_turn = False
        self._input_text = self._output_text = ""
        self._input_open = self._output_open = False

    def _finish_input(self) -> None:
        if self._input_open:
            self._events.put(InputText(self._input_text, True))
        self._input_text, self._input_open = "", False

    def _finish_output(self) -> None:
        if self._output_open:
            self._events.put(OutputText(self._output_text, True))
        self._output_text, self._output_open = "", False

    async def _handle(self, session, message) -> Optional[str]:
        update = getattr(message, "session_resumption_update", None)
        if update is not None:
            self._store.update(update.new_handle, update.resumable)

        away = getattr(message, "go_away", None)
        if away is not None:
            seconds = parse_seconds(away.time_left)
            log.info("gemini_live go_away seconds=%d", int(seconds))
            self._events.put(GoAway(seconds))
            self._goaway.arm(seconds)
            if not self._in_turn:
                return "reconnect"

        call = getattr(message, "tool_call", None)
        if call is not None:
            for function_call in call.function_calls or []:
                self._events.put(ToolActivity(str(function_call.name or "")))
                key = str(function_call.id or id(function_call))
                self._tools[key] = asyncio.create_task(self._run_tool(session, function_call, key))

        cancel = getattr(message, "tool_call_cancellation", None)
        if cancel is not None:
            for key in cancel.ids or []:
                task = self._tools.pop(str(key), None)
                if task is not None:
                    task.cancel()

        content = getattr(message, "server_content", None)
        if content is None:
            return None

        if content.interrupted:
            dropped = self._events.drop_audio()
            self._finish_output()
            self._in_turn = False
            log.info("gemini_live interrupted dropped_chunks=%d", dropped)
            self._events.put(Interrupted())
            return None

        if content.input_transcription is not None and content.input_transcription.text:
            self._input_text += content.input_transcription.text
            self._input_open = True
            done = bool(content.input_transcription.finished)
            self._events.put(InputText(self._input_text, done))
            if done:
                self._input_text, self._input_open = "", False

        turn = content.model_turn
        if turn is not None:
            for part in turn.parts or []:
                data = getattr(getattr(part, "inline_data", None), "data", None)
                if data:
                    if not self._in_turn:
                        self._finish_input()
                    self._in_turn = True
                    self._events.put(AudioOut(bytes(data)))

        if content.output_transcription is not None and content.output_transcription.text:
            if not self._in_turn:
                self._finish_input()
            self._in_turn = True
            self._output_text += content.output_transcription.text
            self._output_open = True
            done = bool(content.output_transcription.finished)
            self._events.put(OutputText(self._output_text, done))
            if done:
                self._output_text, self._output_open = "", False

        if content.turn_complete:
            self._finish_input()
            self._finish_output()
            self._in_turn = False
            notice = errors.classify_turn_reason(content.turn_complete_reason)
            if notice is not None:
                log.info("gemini_live turn_notice reason=%s", notice.reason_class)  # never a failure
            self._events.put(TurnComplete())
            if self._goaway.armed:
                return "reconnect"
        return None

    async def _run_tool(self, session, call, key: str) -> None:
        name = str(call.name or "")
        started = self._clock()
        try:
            if self.tool_executor is None:
                result: dict = {"error": "no_tools"}
            else:
                result = await self.tool_executor(name, dict(call.args or {}))
            if not isinstance(result, dict):
                result = {"result": str(result)}
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            result = {"error": "tool_failed"}
        finally:
            self._tools.pop(key, None)
        log.info("gemini_live tool=%s ms=%d", name, int((self._clock() - started) * 1000))
        if self._session is not session or not self._connected.is_set():
            return  # the connection was replaced; its call ids are void
        try:
            await session.send_tool_response(
                function_responses=[types.FunctionResponse(id=call.id, name=name, response=result)]
            )
        except Exception:  # noqa: BLE001
            pass
