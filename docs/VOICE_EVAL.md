# Voice evaluation

Written 2026-10-15. **Nobody has listened to any voice in this evaluation.** What was measured is latency (edge-tts, run here) and availability (edge-tts voice list, Gemini model list). Pronunciation and naturalness columns say "listen" where only a person can judge. The samples are generated into `tools/voice_eval/samples/` (not committed) from the fixed set in `tools/voice_eval/sentences.json`; rerun with `venv\Scripts\python tools\voice_eval\edge_samples.py`.

## The fixed sentence set (three sentences per language)

1. Greeting with a Fibre bill: a rupee amount with decimals (Rs. 4,590.50), a date (25 October 2026), the English word Fibre inside the sentence.
2. Phone numbers and short codes: 0112 123 456, 1212, plus "PEOTV data bundle", 25 GB, 100 Mbps.
3. Sri Lankan place names: Colombo, Kandy, Galle, Jaffna, Nuwara Eliya, and a time (8.30).

What to listen for: place names with local pronunciation (Nuwara Eliya, not "noo-wara"), "Rs." read as rupees, digits read one by one for phone numbers, GB and Mbps read as "gigabytes" and "megabits per second" or the English letters naturally, no stumbling at the Latin words inside Sinhala and Tamil.

## Candidates

### Tier 3 cloud voice: edge-tts (Microsoft Edge read aloud service, unofficial)

Licence of the library: LGPL-3.0. The Microsoft service terms for commercial use are unverified (already flagged in THIRD_PARTY.md). Cost: none. Voices found by `list_voices()` on 2026-10-15:

| Language | Female | Male | Notes |
|-|-|-|-|
| en | en-US-AvaMultilingualNeural, en-US-AriaNeural | en-US-AndrewMultilingualNeural, en-US-GuyNeural | |
| si | si-LK-ThiliniNeural | si-LK-SameeraNeural | only these two exist |
| ta | ta-LK-SaranyaNeural, ta-IN-PallaviNeural, ta-MY-KaniNeural, ta-SG-VenbaNeural | ta-LK-KumarNeural, ta-IN-ValluvarNeural, ta-MY-SuryaNeural, ta-SG-AnbuNeural | Sri Lankan Tamil (ta-LK) exists for both genders |

Time to first audio byte, seconds, one run per sentence (measured here, so network dependent):

| Voice | s1 | s2 | s3 |
|-|-|-|-|
| en-US-AvaMultilingualNeural | 3.10 | 6.13 | 6.77 |
| en-US-AndrewMultilingualNeural | 1.98 | 4.54 | 3.10 |
| en-US-AriaNeural | 0.84 | 0.80 | 1.31 |
| en-US-GuyNeural | 0.71 | 1.20 | 0.76 |
| si-LK-ThiliniNeural | 1.49 | 1.72 | 1.64 |
| si-LK-SameeraNeural | 2.58 | 1.56 | 2.09 |
| ta-LK-SaranyaNeural | 1.03 | 2.29 | 1.39 |
| ta-LK-KumarNeural | 1.28 | 2.03 | 2.64 |
| ta-IN-PallaviNeural | 1.62 | 1.08 | 3.27 |
| ta-IN-ValluvarNeural | 1.34 | 2.53 | 2.93 |

**Repeat measurement (2026-10-15, Agent C, `tools/voice_eval/latency_repeat.py`, sentence 2, 6 interleaved runs per voice):**

| Voice | median s | min | max |
|-|-|-|-|
| en-US-AvaMultilingualNeural | 1.08 | 0.92 | 4.80 |
| en-US-AndrewMultilingualNeural | 1.86 | 0.90 | 3.90 |
| en-US-AriaNeural | 0.89 | 0.66 | 4.58 |
| en-US-GuyNeural | 0.98 | 0.74 | 2.71 |
| si-LK-ThiliniNeural | 0.98 | 0.71 | 13.39 |
| si-LK-SameeraNeural | 0.88 | 0.58 | 1.61 |
| ta-LK-SaranyaNeural | 1.14 | 0.55 | 2.13 |
| ta-LK-KumarNeural | 2.22 | 0.55 | 4.34 |

A second single run of `edge_samples.py` an hour later gave 1.3 to 4.8 s for Ava, 2.3 to 3.5 s for Andrew, 1.5 to 2.3 s for Aria and 2.6 to 4.4 s for Guy: every voice was slower and noisier. **Correction to the finding below:** the first measurement exaggerated the gap. Over six runs the medians are within about one second of each other (Aria 0.89 and Guy 0.98 against Ava 1.08 and Andrew 1.86); the spread within one voice (0.7 to 4.6 s for Aria) is larger than the gap between voices. What survives is that Aria and Guy were never the slowest and Andrew had the worst median. The change of default to Aria and Guy is therefore a mild latency argument, not a proven one; the naturalness of Ava and Andrew, which may be better, was not judged by anyone. Decide by ear.

**Finding (first measurement):** the "Multilingual" English voices chosen on 2026-10-14 (Ava, Andrew) take 2 to 7 seconds before the first audio, against under 1.5 seconds for Aria and Guy. In a voice conversation that is a long silence. Recommendation until the owner has listened: English on Aria and Guy (fast), keeping Ava and Andrew as an option for chat read aloud where a pause matters less. This reverses part of the previous day's choice; the reason is measured latency, the claimed naturalness of the multilingual voices is not.

### Tier 2: LiveKit Inference TTS (docs, not run)

| Model | Sinhala | Tamil | Notes |
|-|-|-|-|
| Inworld Realtime TTS 2.0 and 2.0 Flash | yes (the only Inference models listing Sinhala) | yes | 100+ languages; Inworld's own plan has a free tier of about 40 minutes (third party summary) |
| Gemini 3.8 Flash TTS (`google/gemini-3.8-flash-tts`), Flash-Lite (`...-flash-lite-tts`) | the Google model page says Sinhala is supported (130 and 101 languages) but LiveKit's page lists only Tamil for Gemini | yes | no EU endpoint |
| Cartesia Sonic 3, 3.5, 3.6 | no | yes | |
| ElevenLabs flash v2.5 | not listed | yes | |

Checked again by Agent C on 2026-10-15 (docs, not run):

* Model id strings on https://docs.livekit.io/agents/models/tts/ : `inworld/inworld-tts-2`, `inworld/inworld-tts-2-flash` (also `inworld/inworld-tts-1.5-max`, `-1.5-mini`), `google/gemini-3.8-flash-tts`, `google/gemini-3.8-flash-lite-tts`. The voice is appended after a colon, example `inworld/inworld-tts-1:Ashley`.
* Inworld page https://docs.livekit.io/agents/models/tts/inworld/ : TTS 2.0 lists `ta` and `si` among 80 plus language codes. Voices named on the page: Ashley (American female), Diego (Mexican male), Edward (American male), Olivia (British female). The page only samples the list ("explore the available voices in the Inworld TTS Playground"), so **no Sinhala specific voice id could be confirmed**; `config/voices.yaml` uses Ashley and Edward for Sinhala as the first thing to try. Whether an American English voice speaks Sinhala well is unknown.
* Gemini page https://docs.livekit.io/agents/models/tts/gemini/ : default model `gemini-3.1-flash-tts-preview`, also `gemini-3.8-flash-tts` and `gemini-3.8-flash-lite-tts`; voices named: Zephyr and Kore (default); the page refers to the Google speech generation page for the full list and gives no per language support or prices.
* Price: neither page states one; both point to the LiveKit pricing page, which was not read.
* The Python SDK installed here (livekit-agents 1.8.4) exposes `livekit.agents.inference.TTS(model, voice=, language=, api_key=, api_secret=, ...)`, which `providers/livekit_inference_tts.py` uses. It was never called against the service.

Free plan credits for LiveKit Inference were not stated in the documentation pages read. Ranking for tier 2: Sinhala Inworld 2.0 (only option); Tamil Inworld 2.0 or Gemini 3.8 Flash (listen); English Gemini 3.8 Flash-Lite or Inworld (listen). Not tested: the LiveKit key in `.env` exists but no Inference TTS call was made in this phase.

### Tier 1: Gemini Live prebuilt voices (docs, not run)

The Live API accepts the same voice names as the Gemini TTS models (30 prebuilt voices, for example Kore, Puck, Charon, Aoede, Leda, Orus, Zephyr, Fenrir), selected in `speechConfig`. The key has no credit, so no voice could be heard. Starting choice, **unverified**: female Kore, male Puck, in all three languages (the same names the Google Cloud Chirp3 map already uses); alternates female Aoede, male Charon. The owner must listen with a funded key; the names are in `config/voices.yaml` and change without code.

### Offline voices (tier 3 last resort)

| Voice | Language/gender | Licence | Cost | Decision |
|-|-|-|-|-|
| Piper `si_LK-dilu-medium` | Sinhala, female only | claimed MIT in the brief; **NOT FOUND**: https://huggingface.co/rhasspy/piper-voices has no `si` directory (tree API: "si does not exist on main"; the repository tags list no Sinhala), so nothing was downloaded and no model card could be read | free, runs on CPU | adapter `providers/piper_si.py` exists and reports unavailable until a model is placed by hand in `data/models/piper_si/` after its licence is read; not default; no male |
| SinhalaVITS (dialoglk) | Sinhala | MPL-2.0 per THIRD_PARTY.md, needs torch and Coqui | free | not adopted |
| `intellisr/sinhala-tts-piper-v1` | Sinhala | tagged GPL-3.0, data rights unclear | n/a | not adopted |
| MMS-TTS Sinhala and Tamil | | CC BY-NC 4.0 | | rejected (non commercial) |
| Windows SAPI5 | English only on this machine | OS | free | last resort |

No open male Sinhala voice with a commercial licence was found in earlier research (docs/RESEARCH.md) and none turned up in the voice list; Sinhala male offline is therefore not available, and the male Sinhala voice on tier 3 is edge-tts only, falling back to the female offline voice or SAPI with a log line.

## Ranking (provisional until someone listens)

| Language / gender | Tier 1 | Tier 2 | Tier 3 |
|-|-|-|-|
| en female | Kore | Gemini 3.8 Flash-Lite TTS (listen) | en-US-AriaNeural, then Ava |
| en male | Puck | same, male voice | en-US-GuyNeural, then Andrew |
| si female | Kore | Inworld 2.0 | si-LK-ThiliniNeural, then Piper dilu |
| si male | Puck | Inworld 2.0 | si-LK-SameeraNeural |
| ta female | Kore | Gemini 3.8 Flash or Inworld 2.0 | ta-LK-SaranyaNeural, then ta-IN-PallaviNeural |
| ta male | Puck | same | ta-LK-KumarNeural, then ta-IN-ValluvarNeural |

## Text normalizer (tiers 2 and 3; tier 1 speaks natively)

`backend/voice_text/normalize(text, language)`, words in `backend/voice_text/lexicon.yaml`, called through `backend/voicekit/normalizer_adapter.prepare_for_tts(text, language, tier)`. Decisions:

* English: numbers, years, ordinals, dates, times (a.m. and p.m. spoken as letters, the sentence period kept), rupees and cents, units (gigabytes, megabits per second, percent), phone numbers digit by digit with a comma pause between the groups typed, short codes 1212 and 1222 digit by digit, abbreviations spelled as letters (S L T, P E O T V, four G).
* Sinhala and Tamil: **no number to words**. The joining forms of Sinhala and Tamil numerals are irregular and a wrong numeral is worse than a digit, so integers stay as Arabic digits (thousands commas removed) for the voice to read, while phone numbers, short codes, OTP codes, numbers of 7 or more digits and decimal fractions are spoken digit by digit with the digit words (those are certain). Months are written in the language, dates become year, month, day. Rupees become `රුපියල් N` / `N ரூபாய்` with cents as `සත N` / `N சதம்`. Times become `8:30` with `පෙරවරු/පස්වරු` and `முற்பகல்/பிற்பகல்` for a.m. and p.m.
* Latin abbreviations and English telecom words inside Sinhala and Tamil sentences (Fibre, data bundle, router, WiFi, package, bill, broadband, reload, SLT, PEOTV and others) are respelled in the native script so the voice has something to read. The spellings are mine and **no native speaker has checked them**; a wrong spelling is edited in the yaml. The switch to compare by ear is `VOICE_NORMALIZE=0`.
* English year rule: a bare four digit number from 1900 to 2099 is read as a year ("twenty twenty six"); a quantity such as "1950 customers" would be misread. Dates written day first (25/10/2026) are assumed day/month/year.
* Properties tested: idempotent (it runs to a fixed point), never raises, never grows beyond 8 times plus 64 characters, no digits left in a phone number.

Samples: `tools/voice_eval/normalized_samples.py` writes `<voice>_<i>_raw.mp3` and `<voice>_<i>_norm.mp3` plus `before_after.txt` into `tools/voice_eval/samples/normalized/` (generated 2026-10-15, not committed, not listened to by anyone).

## Sources checked for this section (2026-10-15)

| Source | URL | What it gave |
|-|-|-|
| LiveKit Inference TTS overview | https://docs.livekit.io/agents/models/tts/ | model id strings |
| LiveKit Inworld TTS | https://docs.livekit.io/agents/models/tts/inworld/ | models 2.0 and 2.0 Flash, `si` and `ta` listed, voices Ashley, Diego, Edward, Olivia |
| LiveKit Gemini TTS | https://docs.livekit.io/agents/models/tts/gemini/ | models and the voices Zephyr and Kore, no prices |
| Piper voices repository | https://huggingface.co/rhasspy/piper-voices (API: `/api/models/rhasspy/piper-voices/tree/main/si`) | no Sinhala directory, MIT is the repository tag only |
| edge-tts voice list | `edge_tts.list_voices()` run here | the voice names above |

## Owner action

Listen to `tools/voice_eval/samples/` (two per language and gender, tier 3) and to `tools/voice_eval/samples/normalized/` (raw against normalized, the main thing to judge is Sinhala and Tamil) and tell me which to keep. For tier 1 and tier 2, fund the Gemini key (and enable LiveKit Inference) so the same sentences can be generated; I will add them to the same folder.
