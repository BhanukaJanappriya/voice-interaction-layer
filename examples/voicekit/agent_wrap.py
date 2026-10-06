"""Example 1: give a text agent a voice with speak_agent.

Run from the repository root:

    python examples/voicekit/agent_wrap.py

The "agent" here is a toy that streams its reply in small pieces, the way an
LLM does. Sentences are spoken on your speakers while it is still writing.
Swap in your own object (anything with respond_stream(text) or respond(text))
or a plain function.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))

from voicekit.adapter import speak_agent  # noqa: E402
from voicekit.settings import VoicekitSettings  # noqa: E402
from voicekit.sinks import SpeakerSink  # noqa: E402


class ToyAgent:
    def respond_stream(self, text: str):
        reply = f"You asked about {text}. Here is a short answer. I hope it helps."
        for word in reply.split(" "):
            time.sleep(0.05)  # pretend the model is still thinking
            yield word + " "


if __name__ == "__main__":
    settings = VoicekitSettings(google_credentials_path=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""))
    ask = speak_agent(ToyAgent(), sink=SpeakerSink(), language="en", voice="female_natural", settings=settings)
    print("reply:", ask("opening hours"))
