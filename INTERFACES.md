Interface contracts for the voice interaction layer

These signatures are fixed so the three modules can be built in parallel
without touching each other's code. Do not change them without updating
this file and telling the other modules.

stt.py
    def transcribe_audio(duration: float = 5.0, silence_timeout: float | None = None) -> str
    Records from the default microphone and returns the transcribed text.
    Returns "" (empty string) if no speech was detected or audio was
    unclear. Never raises for a normal empty/silent recording.

agent.py
    class ChatAgent(ABC):
        def respond(self, text_input: str) -> str

    class EchoAgent(ChatAgent):
        No arguments required. Returns the input text unchanged (prefixed
        so it is obviously the echo agent in a demo).

    class LlamaOllamaAgent(ChatAgent):
        def __init__(self, model: str = "llama3.2:latest",
                     host: str = "http://localhost:11434",
                     tool_callback: Callable[[str], str | None] | None = None)
        Calls the local Ollama REST API. tool_callback is an optional hook
        that, if it returns a non None string for the given input, its
        result is included in the prompt context before calling the model
        (this is the stubbed Tool/API/Knowledge Base hook from the
        required flow).

tts.py
    def speak_text(text_input: str) -> None
    Synthesizes text_input to speech and plays it on the system's default
    audio output device. Blocks until playback finishes.

Shared conventions
    Each module prints a one line latency measurement to the console for
    its own operation (transcription time, agent response time, synthesis
    time) so main.py can also print a per turn summary.
    Each module is runnable standalone: python stt.py / python agent.py /
    python tts.py exercises that module only.
