"""Pluggable, multi provider chat agent layer for the voice interaction backend.

Required pipeline: User Voice -> STT -> (Chat Agent -> Tool/API/KB -> Response) -> TTS.

This module keeps the prototype's core contract (a ChatAgent exposes one
method, respond(text_input) -> str) and adds:

    EchoAgent
        Unchanged from the prototype. Trivial agent, no dependencies, used
        to sanity check the pipeline with zero model calls.

    LlamaOllamaAgent
        Unchanged from the prototype. Talks to a locally running Ollama
        server. This is the local fallback every other path degrades to.

    GeminiAgent, GptAgent
        Real cloud LLM adapters (Google Gemini, OpenAI GPT). Each exposes
        the same respond() contract as every other agent; nothing else in
        the codebase needs to know which one is active.

    Orchestrator
        The new piece. When a cloud provider is configured and reachable,
        it runs a small multi agent collaboration: a tool/retrieval agent
        adds context (the same tool_callback hook the prototype stubbed
        out), and a composer agent turns it into the final spoken reply,
        streamed as it is generated. An optional reasoning agent can draft
        an analysis first (CLOUD_REASONING_STAGE, off by default since it
        delays the reply). All roles are served by the same configured
        cloud provider with different prompts, since standing up genuinely
        separate model backends per role is out of scope for this pass (see
        ARCHITECTURE.md). If the cloud provider is not configured, or a
        call to it fails (unreachable, unauthorized, over quota), the
        Orchestrator falls back to the local LlamaOllamaAgent handling the
        full response alone, with no multi agent attempt in that mode, and
        logs which mode served the reply so the frontend and a live demo
        can show it plainly.
"""

import json
import time
from abc import ABC, abstractmethod
from typing import Callable, Iterator, Optional

import requests

import config
from knowledge.prompts import GROUNDING_RULES, with_context


class ChatAgent(ABC):
    @abstractmethod
    def respond(self, text_input: str) -> str:
        """Takes user text, returns the agent's text response."""
        ...


class EchoAgent(ChatAgent):
    """Trivial agent that echoes the input back, clearly labeled. Proves
    the pluggability of the ChatAgent interface with zero dependencies."""

    def respond(self, text_input: str) -> str:
        start = time.perf_counter()
        response = f"Echo: {text_input}"
        elapsed = time.perf_counter() - start
        print(f"Agent latency: {elapsed:.2f}s")
        return response


# Ollama's /api/generate endpoint accepts an optional "system" field that
# overrides whatever system message the model's Modelfile defines, sent
# alongside "prompt" rather than mixed into it, so persona and behavior
# instructions never compete with the actual user text for the model's
# attention. Without this, llama3.2 answering a bare user string has no
# framing at all: it tends to over-explain, answer more than was asked, and
# add disclaimers, all of which reads badly once spoken aloud by tts.py
# rather than read as text on a screen.
_VOICE_ASSISTANT_SYSTEM_PROMPT = (
    "You are a concise voice assistant. Answer only what the user actually "
    "asked, nothing more and nothing less. Keep replies short and natural, "
    "since they will be spoken aloud rather than read as text. Do not add "
    "disclaimers, caveats, or commentary about being an AI. The user's "
    "message is a live speech to text transcript, so it may be missing "
    "punctuation or have imperfect grammar or phrasing; never comment on, "
    "correct, or apologize for that, just answer what was actually meant. "
    "Usually one to three sentences is enough."
)


# The language each reply must be written in, keyed by the language code
# picked on the frontend's first screen. The script is spelled out because
# a model asked for "Sinhala" alone sometimes answers in romanized
# Sinhala, which the Sinhala voice then reads badly. "guest" is how the
# user is addressed when no name is known, in that language, matching
# the spoken introduction (see _GREETINGS in server.py).
_REPLY_LANGUAGES = {
    "en": {"instruction": "Always reply in English.", "guest": "Guest"},
    "si": {
        "instruction": (
            "Always reply in Sinhala (සිංහල), written in Sinhala script, even if the "
            "user's message contains English words. Keep product names like SLT Mobitel as they are."
        ),
        "guest": "අමුත්තා",
    },
    "ta": {
        "instruction": (
            "Always reply in Tamil (தமிழ்), written in Tamil script, even if the "
            "user's message contains English words. Keep product names like SLT Mobitel as they are."
        ),
        "guest": "விருந்தினர்",
    },
}


def _with_knowledge(system_prompt: str, knowledge_context: Optional[str]) -> str:
    """Adds the SLT Mobitel grounding rules and, when retrieval found
    something, the knowledge base context (with its sources) to a system
    prompt. Without the rules the local model makes up plausible package
    names and prices (seen before this existed: "SLT 50GB, SLT 100GB" for a
    data package question). The wording lives in knowledge/prompts.py, so
    the evaluation in docs/RAG_EVAL.md measures exactly this prompt. On the
    local path this is plain context injection into one model, not multi
    agent collaboration (see ARCHITECTURE.md)."""
    return with_context(system_prompt, knowledge_context)


def _personalize(system_prompt: str, user_name: Optional[str], language: Optional[str] = None) -> str:
    """Adds who the assistant is talking to and which language to answer
    in: replies address the user by the name they gave (on the user
    screen, or by saying "my name is ..." during the conversation, see
    server.py), or as Guest when no name is known, and are written in the
    language picked on the first screen. Put in the system prompt rather
    than the user message for the same reason as the persona itself: it is
    framing, not part of what the user asked. No language (None) keeps the
    old behavior of answering in whatever language the user used."""
    reply_language = _REPLY_LANGUAGES.get(language)
    guest_word = reply_language["guest"] if reply_language else "Guest"
    if user_name:
        prompt = (
            f"{system_prompt} The user's name is {user_name}. Address them by name "
            "naturally, for example when greeting or thanking them, but not in every reply."
        )
    else:
        prompt = (
            f"{system_prompt} The user has not shared their name. If you address them "
            f"directly, call them {guest_word}."
        )
    if reply_language:
        prompt += " " + reply_language["instruction"]
    return prompt


# How long Ollama keeps the model loaded in memory after a request. Its
# default is 5 minutes, after which the next question pays several seconds
# to load the 2GB model from disk again; a demo with pauses between turns
# hit that regularly.
_OLLAMA_KEEP_ALIVE = "30m"


class LlamaOllamaAgent(ChatAgent):
    """Chat agent backed by a local Ollama server. This is the local
    fallback every cloud path degrades to when a cloud provider is not
    configured or not reachable."""

    def __init__(
        self,
        model: str = None,
        host: str = None,
        tool_callback: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.model = model or config.LOCAL_LLM_MODEL
        self.host = (host or config.LOCAL_LLM_HOST).rstrip("/")
        self.tool_callback = tool_callback
        self.timeout_seconds = 60

    def warm_up(self) -> None:
        """Asks Ollama to load the model now (a generate request with no
        prompt only loads it), so the first real question does not wait
        for it. server.py calls this once at startup. Failure is only
        logged: the real request will report the problem properly."""
        start = time.perf_counter()
        try:
            requests.post(
                f"{self.host}/api/generate",
                json={"model": self.model, "keep_alive": _OLLAMA_KEEP_ALIVE},
                timeout=self.timeout_seconds,
            ).raise_for_status()
            print(f"Agent warm up (local Llama): {time.perf_counter() - start:.2f}s")
        except requests.exceptions.RequestException as exc:
            print(f"Agent warm up (local Llama) failed: {exc}")

    def _system_prompt(self, text_input: str, user_name: Optional[str], language: Optional[str]) -> str:
        """The persona, who the user is, the reply language, the grounding
        rules and any retrieved knowledge, all in Ollama's system field.
        Retrieved context used to be prepended to the user prompt; in the
        system prompt it is framing the model answers from, rather than
        text it might read as part of the question."""
        tool_context = None
        if self.tool_callback is not None:
            try:
                tool_context = self.tool_callback(text_input)
            except Exception:
                tool_context = None
        return _with_knowledge(_personalize(_VOICE_ASSISTANT_SYSTEM_PROMPT, user_name, language), tool_context)

    def respond(self, text_input: str, user_name: Optional[str] = None, language: Optional[str] = None) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": text_input,
            "system": self._system_prompt(text_input, user_name, language),
            "stream": False,
            "keep_alive": _OLLAMA_KEEP_ALIVE,
        }

        start = time.perf_counter()
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_seconds)
            elapsed = time.perf_counter() - start
            print(f"Agent latency (local Llama): {elapsed:.2f}s")
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(f"Agent latency (local Llama): {time.perf_counter() - start:.2f}s")
            return "Error: could not reach the local Ollama server. Is it running?"
        except requests.exceptions.Timeout:
            print(f"Agent latency (local Llama): {time.perf_counter() - start:.2f}s")
            return "Error: the local Ollama server took too long to respond."
        except requests.exceptions.HTTPError:
            print(f"Agent latency (local Llama): {time.perf_counter() - start:.2f}s")
            if response.status_code == 404:
                return f"Error: model '{self.model}' was not found on the Ollama server."
            return f"Error: Ollama server returned an HTTP error ({response.status_code})."
        except requests.exceptions.RequestException as exc:
            print(f"Agent latency (local Llama): {time.perf_counter() - start:.2f}s")
            return f"Error: could not get a response from Ollama ({exc})."

        try:
            data = response.json()
        except ValueError:
            return "Error: received an unreadable response from the Ollama server."

        return data.get("response", "").strip()

    def respond_stream(
        self, text_input: str, user_name: Optional[str] = None, language: Optional[str] = None
    ) -> Iterator[str]:
        """Like respond(), but yields response text chunks as Ollama
        generates them (the API's stream mode, newline delimited JSON
        objects each carrying one token or short run of tokens) instead of
        blocking until the full reply is ready. Used by the WebSocket
        voice pipeline so the frontend can show the reply appearing as it
        is actually generated, not just after the fact. Never raises for a
        request level failure; on error it yields a single chunk with the
        same wording respond() would have returned, so callers that just
        concatenate yielded chunks behave the same either way."""
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": text_input,
            "system": self._system_prompt(text_input, user_name, language),
            "stream": True,
            "keep_alive": _OLLAMA_KEEP_ALIVE,
        }

        start = time.perf_counter()
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_seconds, stream=True)
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(f"Agent latency (local Llama, streaming): {time.perf_counter() - start:.2f}s")
            yield "Error: could not reach the local Ollama server. Is it running?"
            return
        except requests.exceptions.Timeout:
            print(f"Agent latency (local Llama, streaming): {time.perf_counter() - start:.2f}s")
            yield "Error: the local Ollama server took too long to respond."
            return
        except requests.exceptions.HTTPError:
            print(f"Agent latency (local Llama, streaming): {time.perf_counter() - start:.2f}s")
            if response.status_code == 404:
                yield f"Error: model '{self.model}' was not found on the Ollama server."
            else:
                yield f"Error: Ollama server returned an HTTP error ({response.status_code})."
            return
        except requests.exceptions.RequestException as exc:
            print(f"Agent latency (local Llama, streaming): {time.perf_counter() - start:.2f}s")
            yield f"Error: could not get a response from Ollama ({exc})."
            return

        try:
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                chunk = data.get("response", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break
        except requests.exceptions.RequestException as exc:
            yield f"\n[Error: connection to Ollama dropped mid reply ({exc}).]"
        finally:
            print(f"Agent latency (local Llama, streaming): {time.perf_counter() - start:.2f}s")


class CloudUnavailableError(Exception):
    """Raised by a cloud agent adapter when the call could not be
    completed (missing key, network failure, auth failure, quota). The
    Orchestrator catches this specifically to trigger local fallback,
    distinguishing "cloud is down, fall back" from a genuine bug."""


_gemini_client = None

# What each Gemini API error code actually means for this app, in plain
# words, so the log and /health say why the cloud path is not being used
# rather than only printing Google's raw error. Codes as observed from the
# live API with the google-genai SDK (errors.APIError.code).
_GEMINI_ERROR_REASONS = {
    400: "the request was rejected (often an invalid API key format or a bad parameter)",
    401: "the API key is invalid or was revoked; create a new one in AI Studio",
    402: "the project's prepaid credits are used up; top up billing in AI Studio",
    403: "the key is not allowed to use this API or model (API disabled, or key restrictions)",
    404: "the model named in GEMINI_MODEL does not exist or is not available to this key; pick one from the list models call",
    429: "the rate limit or quota was hit; wait, or raise the quota in AI Studio",
    500: "Google's service had an internal error; usually temporary",
    503: "Google's service is overloaded or unavailable; usually temporary",
}


def _explain_gemini_error(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    reason = _GEMINI_ERROR_REASONS.get(code)
    if reason is None:
        # No HTTP code at all usually means the request never reached
        # Google: no internet, DNS, proxy or firewall.
        reason = "network problem reaching Google" if code is None else "unexpected error"
    return f"{code or 'no response'}: {reason} (Google said: {str(exc)[:200]})"


class GeminiAgent(ChatAgent):
    """Adapter for Google's Gemini API, using the current google-genai SDK
    (from google import genai; genai.Client). It replaced the deprecated
    google-generativeai package, which Google no longer updates. Only
    imported when actually used, so the rest of the codebase does not
    require it to be installed or configured to run locally."""

    def __init__(self, model: str = None, system_prompt: Optional[str] = None):
        self.model_name = model or config.GEMINI_MODEL
        self.system_prompt = system_prompt

    @staticmethod
    def _client():
        # One client for the whole process: it holds the HTTP connection,
        # so creating one per request would redo the connection setup.
        global _gemini_client
        if not config.GEMINI_API_KEY:
            raise CloudUnavailableError("GEMINI_API_KEY is not set.")
        if _gemini_client is None:
            from google import genai

            _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
        return _gemini_client

    def respond(self, text_input: str) -> str:
        return "".join(self.respond_stream(text_input)).strip()

    def respond_stream(self, text_input: str) -> Iterator[str]:
        """Yields the reply as Gemini generates it
        (client.models.generate_content_stream, the streaming form of
        generate_content), so the voice pipeline can start speaking the
        first sentence before the last one exists. Any failure, before or
        during the stream, is raised as CloudUnavailableError."""
        start = time.perf_counter()
        try:
            from google.genai import types

            stream = self._client().models.generate_content_stream(
                model=self.model_name,
                contents=text_input,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_prompt,
                    # No tools are passed, so the SDK's automatic function
                    # calling has nothing to do; disabling it also stops
                    # the warning it logs on every streamed call.
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            for chunk in stream:
                # chunk.text is None for a chunk with no text part (e.g. only
                # safety metadata).
                if chunk.text:
                    yield chunk.text
            print(f"Agent latency (Gemini, streaming): {time.perf_counter() - start:.2f}s")
        except CloudUnavailableError:
            raise
        except Exception as exc:
            print(f"Agent latency (Gemini): {time.perf_counter() - start:.2f}s")
            raise CloudUnavailableError(f"Gemini call failed: {_explain_gemini_error(exc)}") from exc


class GptAgent(ChatAgent):
    """Adapter for OpenAI's GPT API."""

    def __init__(self, model: str = None, system_prompt: Optional[str] = None):
        self.model_name = model or config.OPENAI_MODEL
        self.system_prompt = system_prompt

    def respond(self, text_input: str) -> str:
        return "".join(self.respond_stream(text_input)).strip()

    def respond_stream(self, text_input: str) -> Iterator[str]:
        """Streaming counterpart of respond(), same contract as
        GeminiAgent.respond_stream()."""
        if not config.OPENAI_API_KEY:
            raise CloudUnavailableError("OPENAI_API_KEY is not set.")

        start = time.perf_counter()
        try:
            from openai import OpenAI

            client = OpenAI(api_key=config.OPENAI_API_KEY)
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append({"role": "user", "content": text_input})

            stream = client.chat.completions.create(model=self.model_name, messages=messages, stream=True)
            for event in stream:
                if event.choices and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
            print(f"Agent latency (GPT, streaming): {time.perf_counter() - start:.2f}s")
        except CloudUnavailableError:
            raise
        except Exception as exc:
            print(f"Agent latency (GPT): {time.perf_counter() - start:.2f}s")
            raise CloudUnavailableError(f"GPT call failed: {exc}") from exc


def _build_cloud_agent(role_prompt: Optional[str] = None) -> ChatAgent:
    """Instantiates the configured cloud provider's agent class for one
    collaboration role. Kept as a small factory so the Orchestrator does
    not need an if/elif per provider at every call site."""
    if config.CLOUD_LLM_PROVIDER == "gpt":
        return GptAgent(system_prompt=role_prompt)
    return GeminiAgent(system_prompt=role_prompt)


_REASONING_ROLE_PROMPT = (
    "You are the reasoning stage of a voice assistant. In two or three short "
    "sentences, identify what the user is actually asking for and any context "
    "needed to answer it well. Do not answer the user directly; only produce "
    "the analysis."
)

_COMPOSER_ROLE_PROMPT = (
    "You are the response stage of a voice assistant. You will be given the "
    "user's message, optionally an internal analysis of what they need, and "
    "optional extra context. Reply directly to the user in natural spoken "
    "language, answering only what they actually asked, nothing more and "
    "nothing less. Keep it concise, usually one to three sentences, with no "
    "mention of any analysis or internal process, and no disclaimers or meta "
    "commentary about being an AI. The user's message is a live speech to text "
    "transcript and may have imperfect grammar or missing punctuation; never "
    "comment on or correct that, just answer what was meant."
)


_CLOUD_RETRY_AFTER_SECONDS = 300


class Orchestrator(ChatAgent):
    """Cloud first, local fallback chat agent.

    When the configured cloud provider is reachable, role prompts against
    that same provider stand in for separate agents:

        tool/retrieval agent -> looks up extra context via tool_callback
                              (the SLT Mobitel knowledge base retriever,
                              knowledge/retriever.py, plugged into the same
                              hook the prototype stubbed out) and folds
                              anything it returns into the conversation.
        composer agent    -> turns the user's message plus any tool
                              context into the final spoken reply, streamed
                              as it is generated.
        reasoning agent   -> optional (CLOUD_REASONING_STAGE=true in .env):
                              drafts a short analysis of what the user
                              needs before the composer runs. Off by
                              default because it is a whole extra model
                              round trip that has to finish before the
                              composer can even start, which roughly
                              doubled the time to the first spoken word,
                              for little gain on short spoken questions.

    If the cloud provider is not configured, or it fails before producing
    any text, the whole attempt is abandoned and LlamaOllamaAgent alone
    produces the full response, exactly as the prototype did, with no
    partial cloud/local mixing. This mirrors a real production fallback:
    half a cloud pipeline plus a local patch would be harder to reason
    about and debug than a clean either/or.
    """

    def __init__(self, tool_callback: Optional[Callable[[str], Optional[str]]] = None):
        self.tool_callback = tool_callback
        self.local_agent = LlamaOllamaAgent(tool_callback=tool_callback)
        self.last_mode: str = "unknown"
        # Circuit breaker: after a cloud failure, go straight to the local
        # agent until this time instead of paying for another failing
        # cloud call (and its timeout) on every turn. A dead key or used up
        # quota rarely fixes itself within a conversation.
        self._cloud_retry_at = 0.0
        # Why the cloud path last failed, in plain words (see
        # _explain_gemini_error), or None once it has worked. Shown by
        # GET /health so the reason is visible without reading logs.
        self.last_cloud_error: Optional[str] = None

    def warm_up(self) -> None:
        """Called once at server startup so the first question does not pay
        for loading models: loads the local Llama model into Ollama and,
        if a cloud provider is configured, sends it one tiny request. That
        both loads its SDK (importing the Gemini SDK alone took several
        seconds here) and finds out right away if the key is not
        usable (bad model name, no credit left), in which case the circuit
        breaker is tripped now instead of on the user's first question."""
        self.local_agent.warm_up()
        if config.cloud_llm_configured():
            start = time.perf_counter()
            try:
                _build_cloud_agent(role_prompt="Reply with one word.").respond("Hi")
                self.last_cloud_error = None
                print(f"Agent warm up (cloud): {time.perf_counter() - start:.2f}s")
            except CloudUnavailableError as exc:
                self.last_cloud_error = str(exc)
                self._cloud_retry_at = time.monotonic() + _CLOUD_RETRY_AFTER_SECONDS
                print(f"Agent warm up: cloud unavailable ({exc}); using the local agent for now.")

    def respond(self, text_input: str, user_name: Optional[str] = None, language: Optional[str] = None) -> str:
        return "".join(self.respond_stream(text_input, user_name, language)).strip()

    def respond_stream(
        self, text_input: str, user_name: Optional[str] = None, language: Optional[str] = None
    ) -> Iterator[str]:
        """Yields the reply in pieces as it is generated, on both the cloud
        and the local path, so the voice pipeline can start speaking the
        first sentence while the rest is still being written.

        The cloud fallback decision is made on the first piece: if the
        cloud call fails before producing any text, the local agent
        answers instead. If it fails after some text has already been
        spoken, switching to a different model mid sentence would sound
        worse than admitting the problem, so a short apology is added to
        what was said instead."""
        cloud_paused_for = self._cloud_retry_at - time.monotonic()
        if config.cloud_llm_configured() and cloud_paused_for > 0:
            print(f"Orchestrator: cloud failed recently, skipping it for another {cloud_paused_for:.0f}s.")
        elif config.cloud_llm_configured():
            cloud_stream = self._stream_cloud(text_input, user_name, language)
            try:
                first_chunk = next(cloud_stream, "")
            except CloudUnavailableError as exc:
                self.last_cloud_error = str(exc)
                self._cloud_retry_at = time.monotonic() + _CLOUD_RETRY_AFTER_SECONDS
                print(f"Orchestrator: cloud path unavailable ({exc}), falling back to local agent alone.")
            else:
                self.last_cloud_error = None
                self.last_mode = f"cloud multi agent ({config.CLOUD_LLM_PROVIDER})"
                print(f"Orchestrator mode: {self.last_mode}")
                yield first_chunk
                try:
                    yield from cloud_stream
                except CloudUnavailableError as exc:
                    print(f"Orchestrator: cloud stream dropped mid reply ({exc}).")
                    yield " Sorry, I lost my connection there. Could you ask that again?"
                return
        else:
            print("Orchestrator: no cloud LLM provider configured, using local agent alone.")

        self.last_mode = "local single agent (Llama via Ollama)"
        print(f"Orchestrator mode: {self.last_mode}")
        yield from self.local_agent.respond_stream(text_input, user_name, language)

    def _stream_cloud(self, text_input: str, user_name: Optional[str], language: Optional[str]) -> Iterator[str]:
        analysis = None
        if config.CLOUD_REASONING_STAGE:
            analysis = _build_cloud_agent(role_prompt=_REASONING_ROLE_PROMPT).respond(text_input)

        tool_context = None
        if self.tool_callback is not None:
            try:
                tool_context = self.tool_callback(text_input)
            except Exception:
                tool_context = None

        composed_prompt = f"User said: {text_input}"
        if analysis:
            composed_prompt += f"\n\nInternal analysis: {analysis}"
        if tool_context:
            composed_prompt += f"\n\nKnowledge base excerpts (from the tool/retrieval stage):\n{tool_context}"

        # The grounding rules go in the composer's role prompt; the context
        # itself travels with the message, as the retrieval stage's output.
        composer_agent = _build_cloud_agent(
            role_prompt=f"{_personalize(_COMPOSER_ROLE_PROMPT, user_name, language)}\n\n{GROUNDING_RULES}"
        )
        yield from composer_agent.respond_stream(composed_prompt)


def _run_interactive_test():
    """Standalone test mode: python agent.py

    Exercises both paths so each can be demonstrated independently:
    - the real cloud path, if a provider is configured
    - a forced local fallback, by talking to LlamaOllamaAgent directly
    Type 'quit' or 'exit' to stop.
    """
    orchestrator = Orchestrator()
    print("Orchestrator test mode. Type 'quit' or 'exit' to stop (Ctrl+C also works).")
    print(f"Cloud provider configured: {config.cloud_llm_configured()} ({config.CLOUD_LLM_PROVIDER})")
    print("Type 'force-local' to switch to a direct LlamaOllamaAgent call for the rest of the session.\n")

    force_local = False
    while True:
        try:
            text_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not text_input.strip():
            continue
        if text_input.strip().lower() in ("quit", "exit"):
            print("Exiting.")
            break
        if text_input.strip().lower() == "force-local":
            force_local = True
            print("Now forcing local only responses (no cloud attempt).")
            continue

        if force_local:
            reply = orchestrator.local_agent.respond(text_input)
        else:
            reply = orchestrator.respond(text_input)
        print(f"Agent [{orchestrator.last_mode if not force_local else 'local single agent (forced)'}]: {reply}")


if __name__ == "__main__":
    _run_interactive_test()
