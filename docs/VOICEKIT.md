# voicekit

Streaming text to speech with word timings, as a Python package
(`backend/voicekit`), an HTTP and WebSocket API, and a browser client
(`frontend/voicekit/voicekit.js`). It speaks English, Sinhala and Tamil, plays
the first words while the rest is still being synthesized, and tells you when
each word is heard so an avatar can move its mouth in step.

voicekit imports nothing from the rest of this project (only the standard
library, numpy and third party libraries), so the `backend/voicekit` folder
can be copied into another codebase. Settings are passed in as an object; it
never reads the environment itself (only the `python -m backend.voicekit`
command line does).

## Five minute quickstart

1. Check it works (needs internet for the free edge-tts voices):

   ```
   python -m backend.voicekit selftest
   ```

   It speaks a sentence in each language and prints the first audio latency.

2. Speak from Python:

   ```python
   from backend.voicekit import synthesize_stream, AudioChunk, WordTiming

   for event in synthesize_stream("Hello there. How are you?", language="en"):
       if isinstance(event, AudioChunk):
           ...   # 16 bit mono PCM bytes in event.pcm at event.sample_rate
       elif isinstance(event, WordTiming):
           print(event.text, event.start_ms)
   ```

3. Serve it over HTTP (any app can call it):

   ```
   set VOICEKIT_API_KEYS=my-secret-key
   python -m backend.voicekit serve --port 8100
   ```

   or mount `create_router(...)` in an existing FastAPI app (see below).

4. Call it:

   ```
   curl -X POST http://127.0.0.1:8100/api/tts/stream ^
     -H "X-API-Key: my-secret-key" -H "Content-Type: application/json" ^
     -d "{\"text\": \"Hello there.\", \"format\": \"wav\"}" -o hello.wav
   ```

Runnable examples are in `examples/voicekit/`:

| File | What it shows |
| --- | --- |
| `agent_wrap.py` | A streaming text agent wrapped with `speak_agent`, spoken on the speakers |
| `http_client.py` | HTTP caller with Python `requests` (and the equivalent curl command), reads word timings |
| `browser_embed.html` | The smallest browser embed with `VoiceKit` |

`frontend/voicekit/demo.html` is a fuller page: it highlights each word as it
is heard and shows a level meter from the analyser node.

## Python API

```python
synthesize_stream(text_or_iterable, language="en", voice="female_natural", settings=None) -> SynthStream
list_voices(settings=None) -> list[dict]
```

`text_or_iterable` is a string or any iterable of string pieces (an LLM reply
still being written). Pieces are cut into sentences with the same rules as
`tts.SentenceChunker` (the very first sentence may be cut at a comma after 40
characters so the first word is not delayed). `language` is `en`, `si` or
`ta`; `voice` is `female_natural` or `male`. An unknown value, empty text, or
text longer than `settings.max_text_chars` raises `InvalidRequest` immediately.
Nothing else is raised: if a provider fails, the next one is tried, and if all
fail the stream ends with `SynthesisDone(provider="none")`.

`SynthStream` is an iterator with a thread safe `cancel()` (also `close()`)
used for barge in. Events (all in `voicekit.types`):

| Event | Fields |
| --- | --- |
| `SentenceStart` | `text`, `sentence_index` |
| `AudioChunk` | `pcm` (16 bit little endian mono), `sample_rate`, `sentence_index` |
| `WordTiming` | `text`, `start_ms`, `duration_ms`, `sentence_index` (start_ms is from the start of that sentence's audio) |
| `SynthesisDone` | `provider`, `first_audio_ms`, `total_ms` (always last) |

Errors: `VoicekitError` (base), `InvalidRequest`, `ProviderUnavailable`
(raised by a provider, caught by the core).

`VoicekitSettings`: `google_credentials_path`, `azure_key`, `azure_region`,
`cloud_provider` (`google` or `azure`), `local_voice_dir`, `max_text_chars`
(2000), `provider_order` (`("cloud", "edge", "local_model", "sapi")`).

A provider that fails before producing audio is skipped for 30 seconds for
that language, so a provider that is out of credit or offline costs one failed
request, not one per sentence. Each synthesis prints
`voicekit first audio (<provider>, <lang>): N ms` (no text, no keys).

### Sinks

All take the event iterator: `sink.play(events)` blocks until delivered;
`sink.cancel()` can be called from another thread and also cancels synthesis.

| Sink | Use |
| --- | --- |
| `SpeakerSink` | Local sound card (sounddevice) |
| `CallbackSink(fn)` | `fn(event)` for every event |
| `HttpChunkSink(format)` | `.stream(events)` yields bytes for a chunked HTTP body (`pcm`, `wav`, `ndjson`) |
| `WebSocketSink(send_bytes, send_json)` | The `/ws/tts` message sequence over any sync sender |
| `LiveKitSink(audio_source, sample_rate=24000, loop=None)` | Feeds a LiveKit `rtc.AudioSource` (anything with `capture_frame` and `clear_queue`) in 10 ms frames; `cancel()` calls `clear_queue()` |

`LiveKitSink.play` must run on a worker thread when `capture_frame` is a
coroutine (as in the real SDK); pass the loop that owns the source as `loop=`.

### speak_agent

```python
ask = speak_agent(agent_or_callable, sink=None, language="en", voice="female_natural")
reply_text = ask("user text")
```

Accepts an object with `respond_stream` or `respond`, or a plain callable,
returning a string or an iterable of strings. The reply is spoken while it
streams and the full text is returned. If the callable accepts a `language`
argument it is passed. `ask.sink.cancel()` interrupts.

## HTTP and WebSocket API

Mount it in a FastAPI app:

```python
from backend.voicekit.api import create_router
from backend.voicekit.settings import VoicekitSettings

app.include_router(create_router(VoicekitSettings(...), lambda: ["key1", "key2"], rate_limit_per_minute=30))
```

### POST /api/tts/stream

Body `{"text", "language"?, "voice"?, "format"?}`; `format` is `ndjson`
(default), `pcm` or `wav`. The response is chunked and starts as soon as the
first audio exists. Header `X-Sample-Rate` carries the sample rate.

* `pcm`: raw 16 bit mono bytes. `wav`: a streaming WAV (the length fields are
  0xFFFFFFFF because the length is unknown; players read to the end).
  Both carry audio only and are resampled to the first chunk's rate if a
  fallback provider uses another one.
* `ndjson`: one JSON object per line: `{"type": "sentence", text,
  sentence_index}`, `{"type": "timing", text, start_ms, duration_ms,
  sentence_index}`, `{"type": "audio", pcm (base64), sample_rate,
  sentence_index}`, and finally `{"type": "done", provider, first_audio_ms,
  total_ms}`. In the API, `start_ms` is measured from the start of the whole
  utterance, not the sentence.

### WS /ws/tts

Client to server: `{"type": "speak", "text", "language", "voice"}` and
`{"type": "cancel"}`. One synthesis at a time per connection.

Server to client: `{"type": "audio_start", "sample_rate"}` (again if the rate
changes), binary frames of 16 bit mono PCM, `{"type": "timings", "words":
[{text, start_ms, duration_ms, sentence_index}]}` (sent just before the audio
they belong to), then `{"type": "done", provider, first_audio_ms, total_ms}`.
`{"type": "cancelled"}` answers a cancel and `{"type": "error", "message"}`
reports a bad request.

### Auth, limits, status codes

* Header `X-API-Key` (the WebSocket also accepts `?api_key=`). Keys are
  compared with `hmac.compare_digest` against every configured key. With no
  keys configured every request is refused.
* 401 missing or wrong key (the WebSocket closes with code 1008), 429 over
  the per key limit (a sliding 60 second window of `rate_limit_per_minute`
  requests; also after 20 failed attempts a minute from one address, or more
  than 8 syntheses at once), 422 bad body, unknown language or voice, empty
  text or text over `max_text_chars`, 503 when no provider could speak.
* Keys and text are never logged; only latency lines are printed. Keys are
  held only as the configured strings and a short digest used as the rate
  limit identity. Put the router behind TLS in production: the key travels in
  a header (or in the URL query for WebSockets, which proxies may log, so
  prefer the header where the client allows it).
* Rate limits are per process and in memory: with several workers each has
  its own counters.

## Browser client

```html
<script src="voicekit.js"></script>
<script>
  const kit = new VoiceKit({ baseUrl: "https://host", apiKey: "...", transport: "http" /* or "ws" */ });
  kit.onTimings = (words) => { /* each word has start_ms, duration_ms, startTime, endTime */ };
  kit.onStart = () => {};  kit.onEnd = ({reason}) => {};  kit.onError = (err) => {};
  await kit.speak("Hello", { language: "en", voice: "female_natural" });
  kit.stop();                         // barge in: stops audio, network and synthesis
</script>
```

Chunks are scheduled back to back on the WebAudio clock, so playback has no
gaps. For lip sync: `kit.analyser` is an `AnalyserNode` on the output;
`kit.audioStartTime` is the `AudioContext` time at which the utterance starts,
so a word is heard at `kit.audioStartTime + start_ms / 1000` (that is the
`startTime` each timing carries); `kit.currentTime` is the playback position
in ms. If the network is slower than playback and a gap occurs, the clock is
shifted by the gap so timings stay aligned. Call `speak()` from a user gesture
(browsers block audio otherwise).

## Providers, in fallback order

| Slot | Provider | Streams? | Word timings | Needs |
| --- | --- | --- | --- | --- |
| cloud | Google Cloud TTS Chirp3-HD | yes, first audio about 0.3 s once connected | no (see below) | service account JSON, paid API |
| cloud | Azure Neural TTS (`cloud_provider="azure"`) | yes | yes | key and region, `azure-cognitiveservices-speech`. Not verified: written from the docs, never run here |
| edge | edge-tts | yes (MP3 decoded incrementally) | yes, per word | internet, no key |
| local_model | Piper through onnxruntime | per sentence | no | model files, `espeak-ng` on PATH. Not verified, see below |
| sapi | Windows SAPI5 via pyttsx3 | per sentence | no | Windows, English only |

All output is converted to 16 bit mono PCM. Google returns no word times
because only SSML marks produce Google timepoints and Chirp3-HD does not
accept SSML marks, so clients should fall back to audio analysis there.
Sinhala has no Google voice. SAPI5 has English voices only here, so
Sinhala and Tamil fall through to `provider="none"` when edge-tts and the
cloud are both down (backend/tts.py speaks an English notice instead; voicekit
does not).

### Voices and licences

`list_voices()` returns `{language, voice, provider, name, license, gender}`.

| Language | Female | Male | Provider |
| --- | --- | --- | --- |
| en | en-US-JennyNeural | en-US-GuyNeural | edge-tts |
| si | si-LK-ThiliniNeural | si-LK-SameeraNeural | edge-tts |
| ta | ta-IN-PallaviNeural | ta-IN-ValluvarNeural | edge-tts |
| en, ta | en-US / ta-IN-Chirp3-HD-Kore | en-US / ta-IN-Chirp3-HD-Puck | Google |
| en | Microsoft Zira | Microsoft David | SAPI5 |

The map is the same as `backend/tts.py`.

* edge-tts voices: Microsoft service terms apply, no key. edge-tts is an
  unofficial client of an endpoint Microsoft does not document or guarantee;
  check the terms before commercial use.
* Google: paid API under the terms of the Google Cloud account.
* SAPI5: voices licensed with Windows.
* Local models: the licence depends on the voice. Where a voice has no
  `<model>.onnx.LICENSE` file, `list_voices()` says "see docs/THIRD_PARTY.md".
  Piper voices carry the licence of the dataset they were trained on, which
  varies from permissive to non commercial. No model is bundled, none was
  licence checked, and the provider only runs when files are present, so
  nothing is enabled by default. docs/RESEARCH.md did not exist when this was
  written; when it lists a verified permissive English female and male voice,
  put the files in `settings.local_voice_dir` as `en_female.onnx` plus
  `en_female.onnx.json` (and `en_male...`), and add the licence name as the
  first line of `en_female.onnx.LICENSE`. Kokoro (Apache-2.0 model) is not
  implemented: it needs the same kind of phonemizer and a different input
  format.

## Limitations

* edge-tts depends on the internet and an unofficial endpoint. Measured first
  audio is about 0.6 s median but single calls took 1.2 s to 9 s on a bad
  moment; there is no cache, so repeated lines are synthesized again.
* Word timings exist only for edge-tts (and Azure, unverified). Sinhala and
  Tamil timings come from edge-tts as well; Google gives none.
* Timings are provider reported word boundaries, not measured against the
  decoded audio; the MP3 decoder adds no offset that voicekit corrects for.
* Sentence N+1 starts synthesizing as soon as sentence N has been delivered
  to the queue (up to 128 events buffer ahead), not truly in parallel with
  it; edge-tts is faster than real time so this is usually seamless.
* Resampling between providers is linear interpolation; it is only used when
  a fallback changes the sample rate in the middle of a reply.
* Rate limits are in memory per process.
* The local model provider and the Azure provider have never been run.

## Running as a standalone service

`python -m backend.voicekit serve --host 0.0.0.0 --port 8100` reads
`VOICEKIT_API_KEYS`, `GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_SPEECH_KEY`,
`AZURE_SPEECH_REGION` and `VOICEKIT_LOCAL_VOICE_DIR` from the environment.
Other commands: `voices` (list voices with licences), `speak TEXT --language
si --voice male --out out.wav`.
