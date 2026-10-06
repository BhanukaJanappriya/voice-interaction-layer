"""Typed errors for the LiveKit transport.

Callers (the worker loop, the app lifespan, tests) need to tell "LiveKit is
not set up" from "LiveKit is down right now" from "this one conversation
broke", because each is handled differently: the first is a configuration
message and no retry, the second is a retry with backoff, the third ends one
session and leaves the worker running. A plain Exception would force string
matching, so each case has its own type under one base class.
"""


class TransportError(Exception):
    """Base class for every error this package raises on purpose."""


class LiveKitNotConfigured(TransportError):
    """LIVEKIT_URL, LIVEKIT_API_KEY or LIVEKIT_API_SECRET is missing. Never
    retried: nothing changes until the environment does."""


class LiveKitUnavailable(TransportError):
    """The LiveKit server cannot be reached (down, wrong URL, bad
    credentials, network). Retried with backoff by the worker loop."""


class SessionError(TransportError):
    """One conversation failed (no microphone arrived, a required piece
    such as voicekit is missing). Ends that session only."""
