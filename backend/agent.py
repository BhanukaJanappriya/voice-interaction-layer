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
        it runs a small multi agent collaboration: a reasoning agent drafts
        an analysis of the user's request, a tool/retrieval agent adds
        context (the same tool_callback hook the prototype stubbed out),
        and a composer agent turns both into the final spoken reply. All
        three roles are served by the same configured cloud provider with
        different prompts, since standing up genuinely separate model
        backends per role is out of scope for this pass (see
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
    "disclaimers, caveats, or commentary about being an AI."
)


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

    def respond(self, text_input: str) -> str:
        prompt = text_input
        if self.tool_callback is not None:
            try:
                tool_context = self.tool_callback(text_input)
            except Exception:
                tool_context = None
            if tool_context is not None:
                prompt = f"Context: {tool_context}\n\nUser: {text_input}"

        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": _VOICE_ASSISTANT_SYSTEM_PROMPT,
            "stream": False,
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

    def respond_stream(self, text_input: str) -> Iterator[str]:
        """Like respond(), but yields response text chunks as Ollama
        generates them (the API's stream mode, newline delimited JSON
        objects each carrying one token or short run of tokens) instead of
        blocking until the full reply is ready. Used by the WebSocket
        voice pipeline so the frontend can show the reply appearing as it
        is actually generated, not just after the fact. Never raises for a
        request level failure; on error it yields a single chunk with the
        same wording respond() would have returned, so callers that just
        concatenate yielded chunks behave the same either way."""
        prompt = text_input
        if self.tool_callback is not None:
            try:
                tool_context = self.tool_callback(text_input)
            except Exception:
                tool_context = None
            if tool_context is not None:
                prompt = f"Context: {tool_context}\n\nUser: {text_input}"

        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": _VOICE_ASSISTANT_SYSTEM_PROMPT,
            "stream": True,
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


class GeminiAgent(ChatAgent):
    """Adapter for Google's Gemini API. Only imports the google
    generativeai SDK when actually used, so the rest of the codebase does
    not require it to be installed or configured to run locally."""

    def __init__(self, model: str = None, system_prompt: Optional[str] = None):
        self.model_name = model or config.GEMINI_MODEL
        self.system_prompt = system_prompt

    def respond(self, text_input: str) -> str:
        if not config.GEMINI_API_KEY:
            raise CloudUnavailableError("GEMINI_API_KEY is not set.")

        start = time.perf_counter()
        try:
            import google.generativeai as genai

            genai.configure(api_key=config.GEMINI_API_KEY)
            model = genai.GenerativeModel(
                self.model_name,
                system_instruction=self.system_prompt,
            )
            result = model.generate_content(text_input)
            elapsed = time.perf_counter() - start
            print(f"Agent latency (Gemini): {elapsed:.2f}s")
            return (result.text or "").strip()
        except CloudUnavailableError:
            raise
        except Exception as exc:
            print(f"Agent latency (Gemini): {time.perf_counter() - start:.2f}s")
            raise CloudUnavailableError(f"Gemini call failed: {exc}") from exc


class GptAgent(ChatAgent):
    """Adapter for OpenAI's GPT API."""

    def __init__(self, model: str = None, system_prompt: Optional[str] = None):
        self.model_name = model or config.OPENAI_MODEL
        self.system_prompt = system_prompt

    def respond(self, text_input: str) -> str:
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

            result = client.chat.completions.create(model=self.model_name, messages=messages)
            elapsed = time.perf_counter() - start
            print(f"Agent latency (GPT): {elapsed:.2f}s")
            return (result.choices[0].message.content or "").strip()
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


class Orchestrator(ChatAgent):
    """Cloud first, local fallback chat agent.

    When the configured cloud provider is reachable, three role prompts
    against that same provider stand in for a reasoning agent, a tool or
    retrieval agent, and a response composer:

        reasoning agent   -> drafts a short analysis of what the user
                              actually needs, in plain terms.
        tool/retrieval agent -> looks up extra context via tool_callback
                              (a stub today, same hook as the prototype)
                              and folds anything it returns into the
                              conversation.
        composer agent    -> turns the reasoning draft plus any tool
                              context into the final spoken reply: concise,
                              natural, no meta commentary about its own
                              process.

    If the cloud provider is not configured, or any call to it raises
    CloudUnavailableError, the whole multi agent attempt is abandoned and
    LlamaOllamaAgent alone produces the full response, exactly as the
    prototype did, with no partial cloud/local mixing. This mirrors a real
    production fallback: half a cloud pipeline plus a local patch would be
    harder to reason about and debug than a clean either/or.
    """

    def __init__(self, tool_callback: Optional[Callable[[str], Optional[str]]] = None):
        self.tool_callback = tool_callback
        self.local_agent = LlamaOllamaAgent(tool_callback=tool_callback)
        self.last_mode: str = "unknown"

    def respond(self, text_input: str) -> str:
        if config.cloud_llm_configured():
            try:
                reply = self._respond_multi_agent_cloud(text_input)
                self.last_mode = f"cloud multi agent ({config.CLOUD_LLM_PROVIDER})"
                print(f"Orchestrator mode: {self.last_mode}")
                return reply
            except CloudUnavailableError as exc:
                print(f"Orchestrator: cloud path unavailable ({exc}), falling back to local agent alone.")
        else:
            print("Orchestrator: no cloud LLM provider configured, using local agent alone.")

        self.last_mode = "local single agent (Llama via Ollama)"
        print(f"Orchestrator mode: {self.last_mode}")
        return self.local_agent.respond(text_input)

    def respond_stream(self, text_input: str) -> Iterator[str]:
        """Streaming counterpart to respond(), used by the WebSocket voice
        pipeline so the reply can be shown appearing in real time as it is
        generated, rather than only after the full text is ready.

        Only the local fallback path is actually streamed chunk by chunk
        here. The cloud multi agent path (reasoning, then tool/retrieval,
        then composing) still runs its three calls to completion and
        yields the finished composed reply as one chunk; streaming a three
        stage pipeline chunk by chunk, and deciding what a viewer should
        see of the internal reasoning stage while it is in flight, was
        judged out of scope for this pass. This keeps the honest
        distinction intact: cloud path correctness has been reviewed but
        not run live here (see ARCHITECTURE.md), and this does not pretend
        otherwise by making it merely look streamed."""
        if config.cloud_llm_configured():
            try:
                reply = self._respond_multi_agent_cloud(text_input)
                self.last_mode = f"cloud multi agent ({config.CLOUD_LLM_PROVIDER})"
                print(f"Orchestrator mode: {self.last_mode}")
                yield reply
                return
            except CloudUnavailableError as exc:
                print(f"Orchestrator: cloud path unavailable ({exc}), falling back to local agent alone.")
        else:
            print("Orchestrator: no cloud LLM provider configured, using local agent alone.")

        self.last_mode = "local single agent (Llama via Ollama)"
        print(f"Orchestrator mode: {self.last_mode}")
        yield from self.local_agent.respond_stream(text_input)

    def _respond_multi_agent_cloud(self, text_input: str) -> str:
        reasoning_agent = _build_cloud_agent(
            role_prompt=(
                "You are the reasoning stage of a voice assistant. In two or three short "
                "sentences, identify what the user is actually asking for and any context "
                "needed to answer it well. Do not answer the user directly; only produce "
                "the analysis."
            )
        )
        analysis = reasoning_agent.respond(text_input)

        tool_context = None
        if self.tool_callback is not None:
            try:
                tool_context = self.tool_callback(text_input)
            except Exception:
                tool_context = None

        composer_agent = _build_cloud_agent(
            role_prompt=(
                "You are the response stage of a voice assistant. You will be given the "
                "user's message, an internal analysis of what they need, and optional extra "
                "context. Reply directly to the user in natural spoken language, answering "
                "only what they actually asked, nothing more and nothing less. Keep it "
                "concise, with no mention of the analysis or internal process, and no "
                "disclaimers or meta commentary about being an AI."
            )
        )
        composed_prompt = f"User said: {text_input}\n\nInternal analysis: {analysis}"
        if tool_context:
            composed_prompt += f"\n\nAdditional context: {tool_context}"

        return composer_agent.respond(composed_prompt)


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
