"""LiveKit Inference as the cloud language model (CLOUD_LLM_PROVIDER=livekit).

LiveKit Cloud runs a hosted, OpenAI compatible gateway for language models
(https://agent-gateway.livekit.cloud/v1). It is called with the same LiveKit
API key and secret the voice transport already uses: each request carries a
short lived token signed with them that has the "inference" grant, so no
separate model provider key is needed. The Orchestrator treats it like Gemini or
GPT: it is tried first, and when it fails (no credit, network, gateway error)
the cloud is skipped for 5 minutes and the local Ollama model answers alone
(see agent.py's circuit breaker).

Why this is a separate module and not part of agent.py: agent.py already holds
one class per cloud provider, and this one needs the livekit-api package, which
is only imported when this provider is actually used, like the Gemini SDK.

Runnable standalone: python backend/livekit_inference.py "What is two plus two?"
prints the reply and its first token latency.
"""

import datetime
import threading
import time
from typing import Iterator, Optional

import config
from agent import ChatAgent, CloudUnavailableError

# The gateway token lives 10 minutes; a client is rebuilt a minute early.
_TOKEN_TTL_SECONDS = 600
_REFRESH_BEFORE_SECONDS = 60

_client_lock = threading.Lock()
_client = None
_client_built_at = 0.0


def _make_token() -> str:
    from livekit import api

    grant = api.access_token.InferenceGrants(perform=True)
    return (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity("agent")
        .with_inference_grants(grant)
        .with_ttl(datetime.timedelta(seconds=_TOKEN_TTL_SECONDS))
        .to_jwt()
    )


def _get_client():
    """One client for the process (it keeps the HTTP connection warm), rebuilt
    shortly before its token expires. Raises CloudUnavailableError when LiveKit
    is not configured."""
    global _client, _client_built_at
    if not config.livekit_configured():
        raise CloudUnavailableError("LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are not all set.")
    with _client_lock:
        age = time.monotonic() - _client_built_at
        if _client is None or age > _TOKEN_TTL_SECONDS - _REFRESH_BEFORE_SECONDS:
            from openai import OpenAI

            # No automatic retries: a failure should reach the Orchestrator's
            # breaker quickly so the local model answers, not wait here.
            _client = OpenAI(
                api_key=_make_token(),
                base_url=config.LIVEKIT_INFERENCE_URL,
                max_retries=0,
                timeout=config.LIVEKIT_LLM_TIMEOUT_SECONDS,
            )
            _client_built_at = time.monotonic()
        return _client


def _reset_client() -> None:
    global _client
    with _client_lock:
        _client = None


class LiveKitInferenceAgent(ChatAgent):
    """Adapter for LiveKit Inference, same contract as GptAgent: any failure,
    before or during the stream, is raised as CloudUnavailableError, never a
    raw SDK exception, so the Orchestrator can fall back."""

    def __init__(self, model: Optional[str] = None, system_prompt: Optional[str] = None):
        self.model_name = model or config.LIVEKIT_LLM_MODEL
        self.system_prompt = system_prompt

    def respond(self, text_input: str) -> str:
        return "".join(self.respond_stream(text_input)).strip()

    def respond_stream(self, text_input: str) -> Iterator[str]:
        start = time.perf_counter()
        first_token_at = None
        try:
            client = _get_client()
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append({"role": "user", "content": text_input})
            stream = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                stream=True,
                max_tokens=config.LIVEKIT_LLM_MAX_TOKENS,
            )
            for event in stream:
                # The last chunk carries usage and may have no choices or an
                # empty delta; only real text is passed on.
                if not event.choices:
                    continue
                delta = getattr(event.choices[0], "delta", None)
                piece = getattr(delta, "content", None) if delta is not None else None
                if piece:
                    if first_token_at is None:
                        first_token_at = time.perf_counter() - start
                    yield piece
            print(
                f"Agent latency (LiveKit Inference {self.model_name}, streaming): first token "
                f"{(first_token_at if first_token_at is not None else time.perf_counter() - start):.2f}s, "
                f"total {time.perf_counter() - start:.2f}s"
            )
        except CloudUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"Agent latency (LiveKit Inference): {time.perf_counter() - start:.2f}s")
            # A rejected token (expired clock skew, revoked key) is not fixed by
            # reusing the same client.
            _reset_client()
            raise CloudUnavailableError(f"LiveKit Inference call failed: {_explain(exc)}") from exc


def _explain(exc: Exception) -> str:
    """A short reason for /health and the log. Never includes the token or key."""
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if status == 401 or status == 403:
        return f"{name} ({status}): the LiveKit key was refused, or the project has no inference access"
    if status == 402 or status == 429:
        return f"{name} ({status}): out of LiveKit Inference credit or rate limited"
    if status:
        return f"{name} ({status})"
    return f"{name}: {str(exc)[:120]}"


if __name__ == "__main__":
    import sys

    question = " ".join(sys.argv[1:]) or "What is two plus two?"
    agent = LiveKitInferenceAgent(system_prompt="You are a concise voice assistant. Answer in one short sentence.")
    print(f"LiveKit configured: {config.livekit_configured()}, model {agent.model_name}")
    print(agent.respond(question))
