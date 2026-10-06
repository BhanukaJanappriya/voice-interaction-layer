"""LiveKit transport for the voice assistant (Part 2, requirement R1).

Additive to /ws/voice, which stays as it is. The page picks a transport from
GET /api/livekit/config; with "livekit" it fetches a token from
POST /api/livekit/token, joins the room, and the agent worker (started from
the app lifespan with start_worker(), or run standalone) joins the same room
and runs the existing VAD, STT, Orchestrator and voicekit pipeline over it.

    from livekit_transport import router, start_worker, Bindings
"""

from .errors import LiveKitNotConfigured, LiveKitUnavailable, SessionError, TransportError
from .session import Bindings, LiveKitVoiceSession, SessionSettings
from .tokens import router, set_agent_status_provider
from .worker import WorkerHandle, build_worker_options, configure, run_in_thread


def start_worker(server_module=None) -> WorkerHandle:
    """What the app lifespan calls when config.VOICE_TRANSPORT == "livekit".
    Pass the server module so the worker reuses its Orchestrator and helpers
    (the same objects /ws/voice uses). Never raises."""
    bindings = Bindings.from_server(server_module) if server_module is not None else None
    return run_in_thread(bindings=bindings)


__all__ = [
    "Bindings",
    "LiveKitNotConfigured",
    "LiveKitUnavailable",
    "LiveKitVoiceSession",
    "SessionError",
    "SessionSettings",
    "TransportError",
    "WorkerHandle",
    "build_worker_options",
    "configure",
    "router",
    "run_in_thread",
    "set_agent_status_provider",
    "start_worker",
]
