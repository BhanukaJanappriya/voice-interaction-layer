"""Azure Neural TTS provider (paid API, optional).

NOT VERIFIED: the Azure Speech SDK is not installed here and there is no key,
so this code follows the SDK documentation but has never been run. It is only
used when settings.cloud_provider is "azure" and a key and region are set,
and any failure falls through to edge-tts like every other provider.

It uses the SDK's synthesizing event (audio arrives in pieces while the
sentence is synthesized) and the word boundary event for timings. Voices are
the same neural voices edge-tts uses, so the one voice map serves both.
"""

import queue
import threading
from typing import Iterator

from ..types import AudioChunk, ProviderUnavailable, WordTiming
from ..voices import edge_voice

NAME = "azure"
SAMPLE_RATE = 24000
_DONE = object()
_TICKS_PER_MS = 10_000.0


def available(settings) -> bool:
    return bool(settings.azure_key and settings.azure_region)


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    if not available(settings):
        raise ProviderUnavailable("Azure key or region not configured")
    try:
        import azure.cognitiveservices.speech as speechsdk
    except Exception as exc:
        raise ProviderUnavailable("azure-cognitiveservices-speech is not installed") from exc

    cfg = speechsdk.SpeechConfig(subscription=settings.azure_key, region=settings.azure_region)
    cfg.speech_synthesis_voice_name = edge_voice(language, voice)
    cfg.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm)
    synth = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
    out: "queue.Queue" = queue.Queue()

    synth.synthesizing.connect(lambda evt: out.put(bytes(evt.result.audio_data)))
    synth.synthesis_word_boundary.connect(
        lambda evt: out.put(
            WordTiming(
                text=evt.text,
                start_ms=evt.audio_offset / _TICKS_PER_MS,
                duration_ms=evt.duration.total_seconds() * 1000.0,
            )
        )
    )

    def run():
        try:
            result = synth.speak_text_async(text).get()
            if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
                out.put(ProviderUnavailable("Azure synthesis did not complete"))
        except Exception as exc:
            out.put(exc)
        finally:
            out.put(_DONE)

    threading.Thread(target=run, daemon=True, name="voicekit-azure").start()
    got_audio = False
    leftover = b""
    while True:
        item = out.get()
        if item is _DONE:
            break
        if isinstance(item, Exception):
            if got_audio:
                break
            raise ProviderUnavailable(f"Azure failed: {type(item).__name__}")
        if cancel.is_set():
            synth.stop_speaking_async()
            return
        if isinstance(item, WordTiming):
            yield item
            continue
        data = leftover + item
        usable = len(data) - (len(data) % 2)
        leftover = data[usable:]
        if usable:
            got_audio = True
            yield AudioChunk(data[:usable], SAMPLE_RATE)
    if not got_audio:
        raise ProviderUnavailable("Azure returned no audio")
