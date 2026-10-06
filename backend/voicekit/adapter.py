"""Give any text agent a voice.

    ask = speak_agent(my_agent, language="en")
    reply = ask("What are your opening hours?")   # speaks while it streams

speak_agent wraps a "ChatAgent" (an object with respond_stream or respond) or
a plain callable, and returns a function from the user's text to the reply
text. While the agent is still writing, completed sentences are already being
spoken through the sink. Nothing here knows about this project's Orchestrator;
it only looks for the two method names, so any agent shaped like that works.
"""

import dataclasses
import inspect
from typing import Callable, Iterable, Iterator

from .core import synthesize_stream
from .settings import VoicekitSettings
from .sinks import Sink, SpeakerSink


def _call(fn: Callable, text: str, language: str):
    """Calls fn(text) and passes language too when fn accepts it."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "language" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(text, language=language)
    return fn(text)


def _reply_source(agent, text: str, language: str):
    """Returns str or an iterable of str, whichever the agent gives."""
    if hasattr(agent, "respond_stream"):
        return _call(agent.respond_stream, text, language)
    if hasattr(agent, "respond"):
        return _call(agent.respond, text, language)
    if callable(agent):
        return _call(agent, text, language)
    raise TypeError("agent must have respond_stream or respond, or be callable")


def speak_agent(
    agent_or_callable,
    sink: Sink = None,
    language: str = "en",
    voice: str = "female_natural",
    settings: VoicekitSettings = None,
) -> Callable[[str], str]:
    """Wraps an agent. The returned function speaks the reply through sink
    (a SpeakerSink when none is given), blocks until it has been spoken, and
    returns the full reply text. Call .sink.cancel() on it (the attribute is
    set on the returned function) from another thread to barge in."""
    active_sink = sink or SpeakerSink()
    # The length cap protects the public API; an agent's own reply is trusted.
    agent_settings = dataclasses.replace(settings or VoicekitSettings(), max_text_chars=10**9)

    def ask(text: str) -> str:
        pieces: list = []
        source = _reply_source(agent_or_callable, text, language)

        if isinstance(source, str):
            pieces.append(source)
            feed: Iterable[str] = source
        else:
            def feed() -> Iterator[str]:
                for piece in source:
                    pieces.append(piece)
                    yield piece

            feed = feed()
        if isinstance(feed, str) and not feed.strip():
            return ""
        active_sink.play(synthesize_stream(feed, language=language, voice=voice, settings=agent_settings))
        return "".join(pieces)

    ask.sink = active_sink
    return ask
