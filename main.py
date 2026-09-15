"""Entry point for the voice interaction prototype.

Runs the full loop: record voice, transcribe, send to the pluggable chat
agent, receive a response, synthesize speech, play it back, then repeat.

To switch which chat agent handles the conversation, change AGENT below.
That is the only line that needs to change; nothing in stt.py or tts.py
is affected, since both agents share the same ChatAgent.respond() shape.
"""

import time

from agent import EchoAgent, LlamaOllamaAgent
from stt import transcribe_audio
from tts import speak_text

# Change this single line to plug in a different chat agent.
AGENT = LlamaOllamaAgent()
# AGENT = EchoAgent()

RECORD_DURATION = 8.0
SILENCE_TIMEOUT = 2.0


def run_turn() -> bool:
    """Runs one record, think, speak cycle. Returns False if the turn
    could not proceed (e.g. no speech heard), True otherwise."""
    print("\nListening... speak now.")
    stt_start = time.time()
    user_text = transcribe_audio(duration=RECORD_DURATION, silence_timeout=SILENCE_TIMEOUT)
    stt_time = time.time() - stt_start

    if not user_text:
        print("No speech detected, skipping this turn.")
        return False

    print(f"You said: {user_text}")

    agent_start = time.time()
    try:
        reply_text = AGENT.respond(user_text)
    except Exception as exc:
        print(f"Agent error: the chat agent failed unexpectedly ({exc}).")
        return False
    agent_time = time.time() - agent_start

    if not reply_text:
        print("Agent error: no response text was produced.")
        return False

    print(f"Agent replied: {reply_text}")

    tts_start = time.time()
    try:
        speak_text(reply_text)
    except Exception as exc:
        print(f"TTS error: could not speak the response ({exc}).")
    tts_time = time.time() - tts_start

    print(
        "Latency summary: "
        f"STT {stt_time:.2f}s, agent {agent_time:.2f}s, TTS {tts_time:.2f}s, "
        f"total {stt_time + agent_time + tts_time:.2f}s"
    )
    return True


def main():
    print(f"Voice interaction prototype. Active agent: {type(AGENT).__name__}")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            run_turn()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
