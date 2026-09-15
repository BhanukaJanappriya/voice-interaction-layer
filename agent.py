"""Pluggable chat agent interface for the voice interaction prototype.

Required pipeline: User Voice -> STT -> (Chat Agent -> Tool/API/KB -> Response) -> TTS.

This module defines the ChatAgent abstract base class and two concrete
implementations:

    EchoAgent
        A trivial agent that echoes back the input. It requires no
        constructor arguments and exists purely to prove that any agent
        can slot into the voice pipeline through the same respond()
        interface, with zero changes needed outside this module.

    LlamaOllamaAgent
        Talks to a locally running Ollama server (via the `requests`
        library) to get a response from a local LLM such as
        llama3.2:latest. Accepts an optional tool_callback hook as a
        stand in for a future Tool/API/Knowledge Base integration.

Any current or future chat agent (local LLM, cloud API, rule based
system, etc.) should be implemented as a ChatAgent subclass with a
respond(text_input) -> str method. The voice layer (stt.py / tts.py)
never needs to know which concrete agent it is talking to.
"""

import time
from abc import ABC, abstractmethod
from typing import Callable, Optional

import requests


class ChatAgent(ABC):
    @abstractmethod
    def respond(self, text_input: str) -> str:
        """Takes user text, returns the agent's text response."""
        ...


class EchoAgent(ChatAgent):
    """Trivial agent that echoes the input back, clearly labeled.

    Used to prove the pluggability of the ChatAgent interface: swapping
    this in for LlamaOllamaAgent (or any other agent) requires nothing
    more than instantiating a different class.
    """

    def respond(self, text_input: str) -> str:
        start = time.perf_counter()
        response = f"Echo: {text_input}"
        elapsed = time.perf_counter() - start
        print(f"Agent latency: {elapsed:.2f}s")
        return response


class LlamaOllamaAgent(ChatAgent):
    """Chat agent backed by a local Ollama server.

    Talks to the Ollama REST API directly via `requests` rather than the
    `ollama` pip package, to keep the dependency list small and to avoid
    confusion between the Ollama application and a pip package of the
    same name.
    """

    def __init__(
        self,
        model: str = "llama3.2:latest",
        host: str = "http://localhost:11434",
        tool_callback: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.tool_callback = tool_callback
        self.timeout_seconds = 60

    def respond(self, text_input: str) -> str:
        # Stubbed Tool/API/Knowledge Base hook. If a tool_callback is
        # provided and returns useful context for this input, fold that
        # context into the prompt before calling the model. A real
        # integration (search API, database lookup, etc.) would plug in
        # here without changing anything else in this class.
        prompt = text_input
        if self.tool_callback is not None:
            try:
                tool_context = self.tool_callback(text_input)
            except Exception:
                tool_context = None
            if tool_context is not None:
                prompt = (
                    f"Context: {tool_context}\n\n"
                    f"User: {text_input}"
                )

        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        start = time.perf_counter()
        try:
            response = requests.post(url, json=payload, timeout=self.timeout_seconds)
            elapsed = time.perf_counter() - start
            print(f"Agent latency: {elapsed:.2f}s")
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            elapsed = time.perf_counter() - start
            print(f"Agent latency: {elapsed:.2f}s")
            return "Error: could not reach the local Ollama server. Is it running?"
        except requests.exceptions.Timeout:
            elapsed = time.perf_counter() - start
            print(f"Agent latency: {elapsed:.2f}s")
            return "Error: the local Ollama server took too long to respond."
        except requests.exceptions.HTTPError:
            elapsed = time.perf_counter() - start
            print(f"Agent latency: {elapsed:.2f}s")
            if response.status_code == 404:
                return f"Error: model '{self.model}' was not found on the Ollama server."
            return f"Error: Ollama server returned an HTTP error ({response.status_code})."
        except requests.exceptions.RequestException as exc:
            elapsed = time.perf_counter() - start
            print(f"Agent latency: {elapsed:.2f}s")
            return f"Error: could not get a response from Ollama ({exc})."

        try:
            data = response.json()
        except ValueError:
            return "Error: received an unreadable response from the Ollama server."

        return data.get("response", "").strip()


def _run_interactive_test():
    """Standalone test mode: python agent.py

    Prompts interactively for text input in a loop and prints the
    LlamaOllamaAgent's response, until the user types "quit"/"exit" or
    presses Ctrl+C.
    """
    agent = LlamaOllamaAgent()
    print("LlamaOllamaAgent test mode. Type 'quit' or 'exit' to stop (Ctrl+C also works).")

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

        reply = agent.respond(text_input)
        print(f"Agent: {reply}")


if __name__ == "__main__":
    _run_interactive_test()
