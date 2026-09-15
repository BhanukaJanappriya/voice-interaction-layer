# Architecture: Pluggable Chat Agent Design

This document explains the plug in design of the chat agent layer, which is
the core reusability requirement of this prototype: any current or future
chat agent should slot into the voice pipeline through one interface,
without the voice layer (`stt.py`, `tts.py`, `main.py`'s recording and
playback logic) ever needing to change.

## The interface

All chat agents implement a single abstract base class defined in
`agent.py`:

```python
class ChatAgent(ABC):
    @abstractmethod
    def respond(self, text_input: str) -> str:
        ...
```

That is the entire contract. `main.py` never imports or refers to a
specific agent implementation by name in its pipeline logic; it only calls
`AGENT.respond(user_text)` on whatever object `AGENT` currently holds. As
far as `main.py`, `stt.py`, and `tts.py` are concerned, a `ChatAgent` is a
black box that takes text in and returns text out.

## Why this satisfies the pluggability requirement

Two concrete agents are implemented today, and both prove the same point
from opposite directions:

- `EchoAgent` has no dependencies, no network calls, and no constructor
  arguments. It exists purely to demonstrate that the pipeline runs
  correctly with a trivial agent, with zero special casing anywhere else
  in the codebase.
- `LlamaOllamaAgent` talks to a real local LLM over HTTP, handles network
  failures and timeouts, and accepts an optional `tool_callback` hook for
  a future Tool/API/Knowledge Base step. Despite being far more complex
  internally, it exposes exactly the same one method surface as
  `EchoAgent`.

Switching which agent is active is a one line change in `main.py`:

```python
AGENT = LlamaOllamaAgent()
# AGENT = EchoAgent()
```

No other file needs to change. This was verified directly: the full
pipeline was run once with each agent and produced correct output in both
cases, with identical STT and TTS behavior.

## How to add a new chat agent

A future engineer adding, for example, a cloud hosted GPT model, a
company internal API, or a rule based FAQ bot, follows the same three
steps every time:

1. Create a new class in `agent.py` (or a new module, as long as it is
   imported where `main.py` picks the active agent) that subclasses
   `ChatAgent` and implements `respond(self, text_input: str) -> str`.
2. Inside `respond()`, do whatever is needed to produce a reply: call an
   API, query a database, run a local model, apply rules. The internals
   are entirely up to the implementation.
3. Point `AGENT` in `main.py` at the new class.

No changes to `stt.py`, `tts.py`, or the recording/playback logic in
`main.py` are ever required, because those modules only depend on the
`ChatAgent` interface, never on a specific implementation.

## The Tool / API / Knowledge Base hook

The required flow is:

```
User Voice -> STT -> (Chat Agent -> Tool/API/KB -> Response) -> TTS
```

`LlamaOllamaAgent` accepts an optional `tool_callback` at construction
time: a function that takes the user's text and returns either a string
of extra context or `None`. When context is returned, it is folded into
the prompt sent to the model before the model call happens. This is a
working stub today (no real external tool is wired in), but it
demonstrates precisely where a real Tool, API, or Knowledge Base
integration plugs into the architecture, and it does so without changing
the `ChatAgent` interface itself: any agent implementation can choose to
accept and use a similar hook, or ignore the concept entirely, since the
only thing `main.py` and the voice layer require is `respond()`.

## Design tradeoff

The interface is intentionally minimal (`str -> str`, one method). This
was a deliberate choice for the prototype: a synchronous, stateless
contract is the smallest thing that can prove the pluggability
requirement while remaining trivial to reason about and demo. It does
mean conversation history and streaming are not part of the interface
today; both could be added as optional method or constructor
extensions later (see README.md's suggested next steps) without breaking
existing agents, since Python allows new optional parameters with
defaults to be added without touching call sites that don't use them.
