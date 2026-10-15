"""LiveSessionConfig: everything one Live session needs, built by the caller.

The api key is held here only to open the connection. repr() hides it so a
stray log line or traceback cannot print it.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional

DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_VOICE = "Kore"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
# At 16 kHz PCM16 mono one second is 32000 bytes.
BYTES_PER_SECOND_IN = INPUT_SAMPLE_RATE * 2


@dataclass
class LiveSessionConfig:
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    language: str = "en"
    voice_style: str = "female_natural"
    voice_name: str = DEFAULT_VOICE  # the lead maps voice_style to a name from config/voices.yaml
    system_instruction: str = ""
    tools: List[Any] = field(default_factory=list)  # list of google.genai types.Tool (see tools.build_tools)
    resumption_handle: Optional[str] = field(default=None, repr=False)
    setup_timeout_seconds: float = 8.0
    sliding_window: bool = True  # context window compression: lifts the 15 minute audio cap
    max_reconnects: int = 3  # consecutive failed reconnects before the session gives up
    reconnect_buffer_seconds: float = 1.0  # audio kept while reconnecting, never more
    goaway_margin_seconds: float = 2.0  # reconnect this long before the server's deadline
    start_sensitivity_high: bool = False
    end_silence_ms: Optional[int] = None  # None keeps the server default

    def __repr__(self) -> str:
        return (
            f"LiveSessionConfig(model={self.model!r}, language={self.language!r}, voice_name={self.voice_name!r}, "
            f"tools={len(self.tools)}, key={'set' if self.api_key else 'missing'})"
        )
