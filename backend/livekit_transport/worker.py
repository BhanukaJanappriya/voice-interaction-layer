"""The LiveKit agent worker: accepts a job for each new room and runs a
LiveKitVoiceSession in it.

Why livekit-agents only for the worker and not for the conversation: its
AgentSession wants its own STT, LLM and TTS plugins, which would replace
vad.py, stt.py, the Orchestrator and voicekit that the WebSocket path already
uses. So this module borrows only what the framework is good at (registering
with the LiveKit server, receiving dispatches, per job room objects,
lifecycle) and runs our own pipeline inside the job.

Jobs run as THREADS of the host process (JobExecutorType.THREAD), never as
child processes. A child process would load its own copy of the Whisper model
and the knowledge index; a thread shares the ones the app already loaded.

Two ways to run it:
  inside the app   run_in_thread() from the FastAPI lifespan when
                   config.VOICE_TRANSPORT == "livekit"; it never raises into
                   the host, and keeps retrying with backoff while LiveKit is
                   unreachable (the app and /ws/voice keep working meanwhile).
  standalone       python -m backend.livekit_transport.worker start
                   (the livekit-agents CLI: start, dev, connect, ...). This
                   imports server.py for the Orchestrator and helpers, so it
                   loads the models itself; use it only when the app is not
                   already running.

Errors are typed (errors.py): not configured is reported once and the worker
does not start; unreachable is retried; one broken session ends that job only.
"""

import asyncio
import os
import sys
import threading
import time
from typing import Callable, Optional

import config

from .errors import LiveKitNotConfigured, LiveKitUnavailable, SessionError, TransportError
from .session import Bindings, LiveKitVoiceSession, SessionSettings
from .tokens import clean_name, normalise_language, normalise_voice_style

# Whisper is CPU bound, so a handful of simultaneous conversations is what one
# machine can really serve; beyond this the worker tells LiveKit it is full
# (the framework default is a CPU percentage that a busy app would trip).
DEFAULT_MAX_SESSIONS = 4
BACKOFF_START_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0

_bindings_provider: Optional[Callable[[], Bindings]] = None


def configure(bindings: Optional[Bindings] = None, bindings_provider: Optional[Callable[[], Bindings]] = None):
    """Gives the worker the server.py helpers. Pass either a ready Bindings
    or a callable that builds one lazily. Without either, the worker finds the
    already imported server module (see _default_bindings)."""
    global _bindings_provider
    if bindings is not None:
        _bindings_provider = lambda: bindings
    elif bindings_provider is not None:
        _bindings_provider = bindings_provider


def _default_bindings() -> Bindings:
    """The server module is already in sys.modules when the worker runs inside
    the app (as backend.server when started with python -m backend, as server
    when run from inside backend/). Importing it fresh would build a second
    Orchestrator and retriever, so reuse the loaded one and only import it
    when running standalone."""
    module = sys.modules.get("backend.server") or sys.modules.get("server")
    if module is None:
        import importlib

        module = importlib.import_module("backend.server")
    return Bindings.from_server(module)


def get_bindings() -> Bindings:
    return (_bindings_provider or _default_bindings)()


def _settings_from_participant(participant, job_metadata: str = "") -> SessionSettings:
    """The language, voice and name were put in the signed token by
    tokens.py, and the browser cannot change them, so they are trusted. A
    missing attribute falls back to the defaults."""
    attributes = dict(getattr(participant, "attributes", {}) or {})
    return SessionSettings(
        language=normalise_language(attributes.get("language")),
        voice_style=normalise_voice_style(attributes.get("voice_style")),
        user_name=clean_name(attributes.get("user_name")),
        verified=attributes.get("verified") == "true",
        access_verified=attributes.get("access") == "verified",
        greet=attributes.get("greet") != "false",
        participant_identity=participant.identity,
    )


async def entrypoint(ctx) -> None:
    """Called by the framework once per accepted job (one room, one user)."""
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    settings = _settings_from_participant(participant, getattr(ctx.job, "metadata", ""))
    print(f"LiveKit job: room {ctx.room.name}, {'verified user' if settings.verified else 'guest'}, language '{settings.language or 'auto'}'.")
    session = LiveKitVoiceSession(ctx.room, settings, get_bindings())
    try:
        await session.run()
    except SessionError as exc:
        print(f"LiveKit session ended with an error: {exc}")
    except Exception as exc:
        # One conversation failing must not take the worker down.
        print(f"LiveKit session crashed ({exc.__class__.__name__}: {exc}).")
    finally:
        ctx.shutdown("conversation ended")


def build_worker_options(max_sessions: int = DEFAULT_MAX_SESSIONS, port: int = 0):
    """livekit-agents WorkerOptions for this app. Raises LiveKitNotConfigured
    when the URL or keys are missing. port 0 lets the framework's small health
    server pick a free port, so it never collides with the app's own."""
    if not config.livekit_configured():
        raise LiveKitNotConfigured("LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must all be set.")
    from livekit import agents

    def _load(server) -> float:
        return min(1.0, len(server.active_jobs) / max(1, max_sessions))

    return agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=config.LIVEKIT_AGENT_NAME,
        job_executor_type=agents.JobExecutorType.THREAD,
        ws_url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
        load_fnc=_load,
        load_threshold=0.99,
        num_idle_processes=0,
        port=port,
        # The supervisor below owns retrying, with its own backoff and logging.
        max_retry=3,
    )


class WorkerHandle:
    """What run_in_thread returns: stop() asks the worker to drain and exit."""

    def __init__(self, thread: threading.Thread):
        self.thread = thread
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self.state = "starting"  # starting | running | retrying | not_configured | stopped
        self.last_error: Optional[str] = None

    def stop(self, timeout: float = 10.0):
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self.thread.join(timeout)

    def status(self) -> dict:
        """For /health: no secret, no URL, a short sanitised error."""
        return {
            "configured": config.livekit_configured(),
            "state": self.state,
            "last_error": self.last_error,
            "agent_name": config.LIVEKIT_AGENT_NAME,
        }


def _short(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {' '.join(str(exc).split())[:160]}"


async def _supervise(handle: WorkerHandle, options_factory: Callable[[], object]):
    """Runs the worker until stopped. When the server is unreachable the
    framework gives up after max_retry attempts; this loop starts it again
    after a growing delay (2 s up to 60 s) and logs the failure once, then
    again only if the failure changes, so a long LiveKit outage does not
    flood the log."""
    from livekit import agents

    handle._loop = asyncio.get_running_loop()
    handle._stop = asyncio.Event()
    delay = BACKOFF_START_SECONDS
    last_logged: Optional[str] = None
    while not handle._stop.is_set():
        try:
            options = options_factory()
        except LiveKitNotConfigured as exc:
            handle.state, handle.last_error = "not_configured", str(exc)
            print(f"LiveKit worker not started: {exc}")
            return
        server = agents.AgentServer.from_server_options(options)
        run_task = asyncio.ensure_future(server.run(devmode=False))
        stop_task = asyncio.ensure_future(handle._stop.wait())
        started = time.monotonic()
        handle.state = "running"
        try:
            await asyncio.wait({run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if stop_task.done():
                handle.state = "stopped"
                break
            error = run_task.exception() if not run_task.cancelled() else None
            raise LiveKitUnavailable(_short(error) if error else "the worker stopped unexpectedly")
        except LiveKitUnavailable as exc:
            handle.state, handle.last_error = "retrying", str(exc)
            if str(exc) != last_logged:
                last_logged = str(exc)
                print(f"LiveKit worker cannot reach the server ({exc}); retrying with backoff, the rest of the app is unaffected.")
            if time.monotonic() - started > 30:
                delay = BACKOFF_START_SECONDS  # it was healthy for a while
        finally:
            stop_task.cancel()
            try:
                await server.aclose()
            except Exception:
                pass
            if not run_task.done():
                run_task.cancel()
        try:
            await asyncio.wait_for(handle._stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, BACKOFF_MAX_SECONDS)
    handle.state = "stopped"


def run_in_thread(
    bindings: Optional[Bindings] = None,
    options_factory: Optional[Callable[[], object]] = None,
) -> WorkerHandle:
    """Starts the worker on a daemon thread with its own event loop and
    returns at once. Never raises: a missing configuration or an unreachable
    server only changes WorkerHandle.state. Call handle.stop() on app
    shutdown (optional, the thread is a daemon)."""
    if bindings is not None:
        configure(bindings=bindings)
    factory = options_factory or build_worker_options

    handle = WorkerHandle(threading.Thread(target=lambda: None))

    def _run():
        try:
            asyncio.run(_supervise(handle, factory))
        except Exception as exc:
            handle.state, handle.last_error = "stopped", _short(exc)
            print(f"LiveKit worker thread ended: {handle.last_error}")

    handle.thread = threading.Thread(target=_run, name="livekit-worker", daemon=True)
    handle.thread.start()
    return handle


def main(argv: Optional[list] = None) -> None:
    """Standalone entry: python -m backend.livekit_transport.worker [start|dev|connect ...].
    Defaults to "start". Uses the livekit-agents CLI, which runs until
    interrupted."""
    from livekit import agents

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "start")
    sys.argv = [sys.argv[0]] + argv
    try:
        options = build_worker_options(port=int(os.environ.get("LIVEKIT_WORKER_PORT", "8081")))
    except TransportError as exc:
        raise SystemExit(f"LiveKit worker not started: {exc}")
    agents.cli.run_app(options)


if __name__ == "__main__":
    main()
