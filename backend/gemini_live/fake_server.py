"""A scripted fake of the google-genai Live session, so tests need no network.

It has the same shape the real code uses:

    client = server.client()                       # stands in for genai.Client
    async with client.aio.live.connect(model=..., config=...) as session:
        await session.send_realtime_input(audio=types.Blob(...))
        await session.send_tool_response(function_responses=[...])
        async for message in session.receive(): ...   # one turn per call, like the SDK

Messages are real google.genai types.LiveServerMessage objects. A script is a
callable run when a connection opens: it gets the FakeLiveSession and calls
session.feed(...) (now, or later from hooks such as on_audio / on_tool_response).
A remote close raises websockets ConnectionClosedError from receive() and from
the next send, like the real socket.

Used by the tests only; nothing in the app imports this module.
"""

import asyncio
from typing import Any, Callable, List, Optional

from google.genai import types
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

# Message builders


def msg_setup() -> types.LiveServerMessage:
    return types.LiveServerMessage(setup_complete=types.LiveServerSetupComplete())


def msg_audio(pcm: bytes) -> types.LiveServerMessage:
    part = types.Part(inline_data=types.Blob(data=pcm, mime_type="audio/pcm;rate=24000"))
    return types.LiveServerMessage(server_content=types.LiveServerContent(model_turn=types.Content(role="model", parts=[part])))


def msg_input_text(text: str, finished: bool = False) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(input_transcription=types.Transcription(text=text, finished=finished))
    )


def msg_output_text(text: str, finished: bool = False) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(output_transcription=types.Transcription(text=text, finished=finished))
    )


def msg_turn_complete(reason: Optional[str] = None) -> types.LiveServerMessage:
    content = types.LiveServerContent(turn_complete=True)
    if reason:
        content = types.LiveServerContent(turn_complete=True, turn_complete_reason=reason)
    return types.LiveServerMessage(server_content=content)


def msg_interrupted() -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))


def msg_tool_call(*calls: tuple) -> types.LiveServerMessage:
    """calls: (id, name, args) tuples."""
    return types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(id=i, name=n, args=a) for i, n, a in calls])
    )


def msg_tool_cancel(*ids: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(tool_call_cancellation=types.LiveServerToolCallCancellation(ids=list(ids)))


def msg_go_away(seconds: float) -> types.LiveServerMessage:
    return types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left=f"{seconds}s"))


def msg_resumption(handle: Optional[str], resumable: bool = True) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle=handle, resumable=resumable)
    )


def closed_error(code: int = 1000, reason: str = "") -> ConnectionClosedError:
    return ConnectionClosedError(Close(code, reason), None)


# The fake session

_CLOSE = object()


class FakeLiveSession:
    def __init__(self, server: "FakeLiveServer", index: int, model: str, config: Any):
        self.server = server
        self.index = index
        self.model = model
        self.config = config
        self.audio_in: List[bytes] = []
        self.texts_in: List[str] = []
        self.tool_responses: List[list] = []
        self.audio_stream_ended = False
        self.closed = False
        self.on_audio: Optional[Callable[["FakeLiveSession", int], None]] = None
        self.on_tool_response: Optional[Callable[["FakeLiveSession", list], None]] = None
        self._queue: "asyncio.Queue[Any]" = asyncio.Queue()
        self._remote_close: Optional[ConnectionClosedError] = None

    # scripting

    def feed(self, *messages: types.LiveServerMessage) -> None:
        for message in messages:
            self._queue.put_nowait(message)

    def close_remote(self, code: int = 1000, reason: str = "") -> None:
        """The server ends the connection; the next receive/send raises."""
        self._remote_close = closed_error(code, reason)
        self._queue.put_nowait(_CLOSE)

    # the shapes the real session has

    def _check(self) -> None:
        if self._remote_close is not None:
            raise self._remote_close
        if self.closed:
            raise closed_error(1000, "")

    async def send_realtime_input(self, *, audio=None, audio_stream_end=None, text=None, media=None, video=None, activity_start=None, activity_end=None) -> None:
        self._check()
        if audio is not None:
            self.audio_in.append(bytes(audio.data if hasattr(audio, "data") else audio["data"]))
            if self.on_audio:
                self.on_audio(self, len(self.audio_in))
        if audio_stream_end:
            self.audio_stream_ended = True
        if text is not None:
            self.texts_in.append(text)

    async def send_tool_response(self, *, function_responses) -> None:
        self._check()
        items = list(function_responses) if isinstance(function_responses, (list, tuple)) else [function_responses]
        self.tool_responses.append(items)
        if self.on_tool_response:
            self.on_tool_response(self, items)

    async def receive(self):
        """Yields messages until a turn_complete (like the SDK), then returns."""
        while True:
            self._check_closed_for_receive()
            item = await self._queue.get()
            if item is _CLOSE:
                self._queue.put_nowait(_CLOSE)
                raise self._remote_close or closed_error(1000, "")
            yield item
            content = getattr(item, "server_content", None)
            if content is not None and content.turn_complete:
                return

    def _check_closed_for_receive(self) -> None:
        if self.closed and self._queue.empty():
            raise closed_error(1000, "")


class _Connect:
    def __init__(self, server: "FakeLiveServer", model: str, config: Any):
        self.server, self.model, self.config = server, model, config
        self.session: Optional[FakeLiveSession] = None

    async def __aenter__(self) -> FakeLiveSession:
        server = self.server
        server.connect_attempts += 1
        if server.connect_delay:
            await asyncio.sleep(server.connect_delay)
        if server.connect_errors:
            error = server.connect_errors.pop(0)
            if error is not None:
                raise error
        self.session = FakeLiveSession(server, len(server.sessions), self.model, self.config)
        server.sessions.append(self.session)
        script = server.scripts.pop(0) if server.scripts else None
        if script is not None:
            script(self.session)
        return self.session

    async def __aexit__(self, *exc_info) -> bool:
        if self.session is not None:
            self.session.closed = True
            self.session._queue.put_nowait(_CLOSE)
        return False


class _Live:
    def __init__(self, server: "FakeLiveServer"):
        self._server = server

    def connect(self, *, model: str, config: Any = None) -> _Connect:
        return _Connect(self._server, model, config)


class _Aio:
    def __init__(self, server: "FakeLiveServer"):
        self.live = _Live(server)


class FakeClient:
    def __init__(self, server: "FakeLiveServer"):
        self.aio = _Aio(server)


class FakeLiveServer:
    """Holds the scripted behaviour for a sequence of connections."""

    def __init__(self, scripts: Optional[List[Callable[[FakeLiveSession], None]]] = None):
        self.scripts: List[Optional[Callable[[FakeLiveSession], None]]] = list(scripts or [])
        self.connect_errors: List[Optional[BaseException]] = []  # consumed one per connect attempt; None = succeed
        self.connect_delay = 0.0
        self.connect_attempts = 0
        self.sessions: List[FakeLiveSession] = []

    def client(self) -> FakeClient:
        return FakeClient(self)

    def factory(self) -> Callable[[Any], FakeClient]:
        """client_factory for GeminiLiveSession(client_factory=...)."""
        return lambda _cfg: self.client()
