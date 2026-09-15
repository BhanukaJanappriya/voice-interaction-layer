# Voice Interaction Layer (Prototype)

A pluggable voice interaction layer for AI chat agents, built as a proof of
concept at SLT Digital Lab. It demonstrates a working local, offline capable
voice pipeline with a chat agent that can be swapped for any other agent
without touching the voice layer.

## Architecture

```
User Voice
    |
    v
Speech to Text (stt.py, faster-whisper)
    |
    v
Chat Agent (agent.py, pluggable via the ChatAgent interface)
    |
    +-> Tool / API / Knowledge Base (stubbed hook, tool_callback)
    |
    v
Response text
    |
    v
Text to Speech (tts.py, pyttsx3)
    |
    v
User Voice (played back)
```

`main.py` orchestrates this loop. See ARCHITECTURE.md for how the chat agent
plug in point works in detail.

## Setup

1. Install Ollama and pull the local model (already done on this machine,
   included here for anyone setting up fresh):
   ```
   ollama pull llama3.2:latest
   ```
   Confirm the Ollama server is running and reachable at
   `http://localhost:11434` (it usually starts automatically after
   installation on Windows; if not, run `ollama serve`).

2. Create a virtual environment with Python 3.12 and install dependencies:
   ```
   python -m venv venv
   venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
   Python 3.12 was chosen over newer Python versions installed on this
   machine because faster-whisper's dependencies do not yet support them.

3. The first run of `stt.py` downloads the faster-whisper "base" model
   (about 142 MB) from Hugging Face and caches it locally. This needs
   internet access once; every run after that is fully offline. No API
   keys are required anywhere in this project.

## Running

Standalone module tests (verify each piece works in isolation):
```
venv\Scripts\python.exe stt.py
venv\Scripts\python.exe agent.py
venv\Scripts\python.exe tts.py
```

Full pipeline:
```
venv\Scripts\python.exe main.py
```
Speak when prompted, wait for the reply to be read back, and press Ctrl+C
to stop. Which chat agent is active is set by a single line near the top
of `main.py` (`AGENT = LlamaOllamaAgent()` by default; swap in `EchoAgent()`
for a quick sanity check with no model calls involved).

## Why these tools were chosen

**Speech to text: faster-whisper.** Runs fully offline after the one time
model download, is noticeably faster than the reference `openai-whisper`
package on CPU because it uses a CTranslate2 backend, and installed cleanly
with no build tools required on this machine. The "base" model size and
int8 quantization were chosen for a good balance of accuracy and speed on
CPU; inference latency observed was about 0.4 to 0.6 seconds for a few
seconds of audio.

**Text to speech: pyttsx3.** Drives the Windows SAPI5 engine natively, so it
needs no model download and worked reliably on the first run. The tradeoff
is voice naturalness: SAPI5 voices sound noticeably more robotic than a
neural TTS engine like Coqui TTS or Piper. Those alternatives were
considered but carry real setup risk on a short timeline (large model
downloads, and in Coqui's case a heavy torch dependency), so pyttsx3 was the
pragmatic choice to get reliable voice output working now. Swapping in a
neural TTS engine later only requires rewriting `tts.py`, since `main.py`
and `agent.py` do not depend on how speech is synthesized.

**Chat agent: local Ollama running Llama 3.2.** Runs entirely on this
machine with no API key and no per call cost, which fits the requirement
that the base demo needs no cloud credentials.

## Current limitations

- The chat agent is stateless: each turn is an independent request with no
  conversation memory carried across turns.
- The Tool/API/Knowledge Base hook in `agent.py` (`tool_callback`) is a
  working stub, not a real integration; it proves the architecture supports
  this step but does not call any real external service yet.
- Silence detection in `stt.py` uses a fixed RMS amplitude threshold, which
  may need tuning for very quiet or very noisy environments, and Whisper
  can occasionally hallucinate short filler words ("You", "Thank you") on
  ambient room noise that crosses the speech threshold.
- No streaming anywhere in the pipeline: the agent call and the TTS
  playback both block until they finish, so a turn's total latency is the
  sum of STT plus agent plus TTS, typically a few seconds total on this
  machine.
- No wake word detection; recording starts immediately at a fixed duration
  each turn rather than continuously listening.

## Suggested next steps

- Add conversation history so the chat agent has multi turn context.
- Replace the fixed duration recording with continuous listening plus a
  wake word (e.g. via `openwakeword` or Porcupine) so the user does not
  need to wait for a recording window.
- Stream both the LLM response and the TTS output so playback can start
  before the full response is generated, reducing perceived latency.
- Implement a real Tool/API/Knowledge Base integration behind the existing
  `tool_callback` hook, for example a document search or a company
  specific API.
- Add an optional cloud fallback (e.g. a hosted LLM or STT/TTS API) behind
  the same interfaces, for environments where local compute is not
  available, while keeping the local path as the default so no API key is
  required to run the base demo.
