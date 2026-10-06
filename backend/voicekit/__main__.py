"""python -m backend.voicekit [selftest | voices | speak | serve]

    selftest            speak a sample in en, si and ta and print latency
    voices              list every voice with its licence
    speak TEXT          synthesize TEXT (--language, --voice, --out file.wav,
                        or play on the speakers when --out is not given)
    serve               run a standalone API server (needs uvicorn); keys from
                        the VOICEKIT_API_KEYS environment variable, comma
                        separated; Google credentials from
                        GOOGLE_APPLICATION_CREDENTIALS

Settings come from environment variables here only because this is a command
line entry point; the library itself never reads the environment.
"""

import argparse
import os
import sys
import wave

from .core import list_voices, run_selftest, synthesize_stream
from .settings import VoicekitSettings
from .types import AudioChunk


def _env_settings() -> VoicekitSettings:
    return VoicekitSettings(
        google_credentials_path=os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        azure_key=os.environ.get("AZURE_SPEECH_KEY", ""),
        azure_region=os.environ.get("AZURE_SPEECH_REGION", ""),
        local_voice_dir=os.environ.get("VOICEKIT_LOCAL_VOICE_DIR", ""),
    )


def _speak(args) -> int:
    settings = _env_settings()
    events = synthesize_stream(args.text, args.language, args.voice, settings)
    if not args.out:
        from .sinks import SpeakerSink

        SpeakerSink().play(events)
        return 0
    pcm, rate = bytearray(), 0
    for event in events:
        if isinstance(event, AudioChunk):
            if rate and event.sample_rate != rate:
                from .audio import resample_pcm16

                event.pcm = resample_pcm16(event.pcm, event.sample_rate, rate)
            rate = rate or event.sample_rate
            pcm.extend(event.pcm)
    if not pcm:
        print("no provider could speak this text", file=sys.stderr)
        return 1
    with wave.open(args.out, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(pcm))
    print(f"wrote {args.out} ({len(pcm) / 2 / rate:.1f} s at {rate} Hz)")
    return 0


def _serve(args) -> int:
    import uvicorn
    from fastapi import FastAPI

    from .api import create_router

    keys = [k.strip() for k in os.environ.get("VOICEKIT_API_KEYS", "").split(",") if k.strip()]
    if not keys:
        print("set VOICEKIT_API_KEYS (comma separated); without keys every request is refused", file=sys.stderr)
    app = FastAPI(title="voicekit")
    app.include_router(create_router(_env_settings(), lambda: keys, args.rate_limit))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="python -m backend.voicekit")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("selftest")
    sub.add_parser("voices")
    speak = sub.add_parser("speak")
    speak.add_argument("text")
    speak.add_argument("--language", default="en", choices=["en", "si", "ta"])
    speak.add_argument("--voice", default="female_natural", choices=["female_natural", "male"])
    speak.add_argument("--out", default="")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)
    serve.add_argument("--rate-limit", type=int, default=30)
    args = parser.parse_args(argv)

    if args.command == "voices":
        for v in list_voices(_env_settings()):
            print(f"{v['language']} {v['voice']:<15} {v['provider']:<11} {v['name']:<26} {v['license']}")
        return 0
    if args.command == "speak":
        return _speak(args)
    if args.command == "serve":
        return _serve(args)
    return run_selftest()


if __name__ == "__main__":
    raise SystemExit(main())
