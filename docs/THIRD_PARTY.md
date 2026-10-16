# Third party components for Part 2

Written 2026-10-05 by Agent A. Evidence and links for each licence are in docs/RESEARCH.md. "Status" says whether the component is adopted, optional, a build time tool only, or considered and not adopted. Nothing in this list has had legal review.

## 1. Avatar

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| TalkingHead (met4citizen) | main commit `b3e277b3b46f88e557bf28a2c5612a5b04e075c3` (package version 1.7.0, tag v1.7.0 is too old for the avatar) | 3D avatar renderer, lip sync, moods, gestures, gaze | https://github.com/met4citizen/TalkingHead | MIT | Keep copyright notice and licence text | yes | Vendored copy of `modules/` into `frontend/avatar/vendor/` by script, or jsDelivr at the pinned commit | Adopted |
| three.js | 0.180.0 (matches TalkingHead's dependency; latest is r186) | WebGL renderer for TalkingHead, ES module through an import map | https://github.com/mrdoob/three.js | MIT | Keep licence text | yes | jsDelivr `three@0.180.0` or vendored | Adopted |
| HeadAudio | commit `d3af5f9ff86ab6b2b1913d411a4e1922ec101953` (no tagged release) | Audio driven visemes, works for any language | https://github.com/met4citizen/HeadAudio | MIT | Keep licence text | yes | Files `dist/headaudio.min.mjs`, `dist/headworklet.min.mjs`, `dist/model-en-mixed.bin` copied by script | Adopted |
| `mpfb.glb` female avatar | file added in commit `6318ff8c798e4a661fcd70c9373c4ab789c822bb`, SHA-256 `63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c`, 36,815,920 bytes | Female avatar | https://raw.githubusercontent.com/met4citizen/TalkingHead/6318ff8c798e4a661fcd70c9373c4ab789c822bb/avatars/mpfb.glb | CC0 1.0 (stated by the TalkingHead README, built from MakeHuman CC0 assets with MPFB) | none (a credit line is courteous) | yes | Download by script, verify hash, do not commit | Adopted. Residual risk: asset list of clothes and hair not published |
| `male.glb` male avatar (built here) | 19,337,536 bytes, SHA-256 `f9ab3528e0df663fbd96be04394069fa23119f3df53f62169499dfc1c15e7e5d` (this build, with the T-shirt) | Male avatar | built by `frontend/avatar/tools/make_male_avatar.py` from the CC0 inputs listed below | CC0 inputs plus MIT rig and weights, see the composition table below | none required (a credit line is courteous) | yes, subject to the two items marked unconfirmed below | Built with Blender, not committed (18 MB, generated); see `frontend/avatar/tools/MALE_AVATAR.md` | Adopted for the toggle. Two input packs need their CC0 status confirmed by reading the licence file inside each zip |
| MakeHuman system assets, Visemes 02, Faceunits 01 packs | MPFB asset packs, current | Inputs for building the male avatar | https://static.makehumancommunity.org/assets/assetpacks.html , https://files.makehumancommunity.org/functional/visemes02.zip , https://files.makehumancommunity.org/functional/faceunits01.zip | CC0 1.0 for core assets ([licence page](https://static.makehumancommunity.org/about/license.html)); community packs vary, check each | none for CC0 | yes for CC0 | Downloaded inside Blender | Build time input only, only if the male avatar is built |
| MPFB Blender extension | 2.0.17 | Authoring tool for the male avatar | https://extensions.blender.org/add-ons/mpfb/ | Code GPL-3.0, output carries no claim by the authors ([LICENSE.md](https://raw.githubusercontent.com/makehumancommunity/mpfb2/master/LICENSE.md)) | none for output | yes for output | Installed in Blender | Build tool, not shipped |
| Blender | 5.2.2 LTS (installed on the dev machine) | Authoring tool | https://www.blender.org | GPL-3.0, output is the author's | none | yes for output | Already installed | Build tool, not shipped |
| glTF-Transform CLI | latest | Optional Meshopt and WebP compression of the GLB | https://github.com/donmccurdy/glTF-Transform | MIT | Keep licence if redistributed | yes | `npm` on demand | Optional build tool |
| Bundled sample avatars `brunette`, `avaturn`, `avatarsdk`, `vroid` | n/a | none | TalkingHead `avatars/` | CC BY-NC 4.0 or non commercial only | n/a | NO | do not download | Rejected |
| Ready Player Me | n/a | none | n/a | service shut down 31 January 2026 | n/a | n/a | n/a | Rejected |

## 2. Voices and speech models

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| edge-tts | 7.2.8 (already in the venv) | Cloud voices for English, Sinhala, Tamil, word boundaries | https://github.com/rany2/edge-tts | LGPL-3.0 (one file MIT) | Keep licence, allow relinking | yes for the library, Microsoft terms for the unofficial Edge endpoint are UNVERIFIED | pip | Adopted, risk flagged |
| Edge voices `si-LK-ThiliniNeural`, `si-LK-SameeraNeural`, `ta-IN-PallaviNeural`, `ta-IN-ValluvarNeural`, `ta-LK-SaranyaNeural`, `ta-LK-KumarNeural`, `en-US-AriaNeural`, `en-US-GuyNeural`, `en-GB-SoniaNeural`, `en-GB-RyanNeural` | service side | Speech output | Microsoft Edge read aloud service | Microsoft service terms, UNVERIFIED for commercial use | none known | UNVERIFIED | Network call at runtime | Adopted, risk flagged |
| piper-tts | 1.8.0 | Optional local English TTS engine with espeak-ng built in | https://github.com/OHF-Voice/piper1-gpl , https://pypi.org/project/piper-tts/ | GPL-3.0-or-later | Licence text and source offer if conveyed | Only with the GPL decision in RESEARCH.md section 6 | pip, Windows wheel `cp39-abi3-win_amd64` (34 MB) | Optional, owner decision |
| espeak-ng | bundled in the piper wheel | Phonemiser for Piper | https://github.com/espeak-ng/espeak-ng | GPL-3.0 | as above | as above | inside piper-tts | Optional, owner decision |
| Piper voice `en_US-john-medium` (male) | model files, 63.5 MB, 22,050 Hz | Local male English voice | https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/john/medium | Dataset public domain (LibriVox), per model card | none required | yes | Download by script, do not commit | Optional |
| Piper voice `en_US-kristin-medium` (female) | 63.5 MB, 22,050 Hz | Local female English voice | https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/kristin/medium | Dataset public domain (LibriVox), per model card | none required | yes | Download by script, do not commit | Optional |
| Piper voices `en_US-bryce`, `norman` (public domain), `joe`, `kathleen-low` (CC0) | medium | Spare male and female voices | same repository | public domain or CC0 | none | yes | script | Alternates |
| Piper voices `libritts_r`, `en_GB-alba` | medium | Alternates | same repository | CC BY 4.0 | yes, credit the dataset | yes with credit | script | Alternates, not first choice |
| Piper voices `ryan`, `hfc_female`, `hfc_male` (CC BY-NC-SA), `northern_english_male`, `southern_english_female` (CC BY-SA), `lessac` (Blizzard licence), `amy`, `alan`, `jenny_dioco` (unresolved) | n/a | none | same repository | NC, share alike or unclear | n/a | NO or unclear | do not download | Rejected |
| Kokoro-82M ONNX release | `kokoro-v1.0.onnx` 325.5 MB, `voices-v1.0.bin` 28.2 MB | Alternative local English TTS, natural voices | https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1 , https://huggingface.co/hexgrad/Kokoro-82M | Model Apache-2.0 | Keep notice | yes for the model | script | Considered, second choice |
| kokoro-onnx, espeakng-loader, phonemizer | 0.6.1, 0.2.4, 3.4.0 | Runtime for Kokoro | https://github.com/thewh1teagle/kokoro-onnx | MIT, MIT, GPL-3.0 (phonemizer), wrapped espeak-ng GPL-3.0 | as applicable | GPL parts need the same decision | pip | Considered, second choice |
| KittenTTS | nano 0.8 | Tiny English model | https://github.com/KittenML/KittenTTS | Apache-2.0 | Keep notice | yes | not tested | Not tested, not adopted |
| MMS-TTS (Facebook), Sinhala and Tamil | n/a | none | https://huggingface.co/facebook/mms-tts-tam | CC BY-NC 4.0 | n/a | NO | do not use | Rejected |
| Indic Parler-TTS, IndicF5 | n/a | Tamil only, heavy | https://huggingface.co/ai4bharat/indic-parler-tts | Apache-2.0, MIT | Keep notice | yes | not downloaded | Not adopted, too heavy for CPU |
| `intellisr/sinhala-tts-piper-v1` | 20.6 MB, 16 kHz | Possible offline Sinhala | https://huggingface.co/intellisr/sinhala-tts-piper-v1 | Tagged GPL-3.0, dataset rights unclear | unknown | NO until written permission | not downloaded into the project | Not adopted |
| `dialoglk/SinhalaVITS-TTS-*` | n/a | Possible offline Sinhala | https://huggingface.co/dialoglk/SinhalaVITS-TTS-M1 | MPL-2.0, needs torch and Coqui TTS, competitor operator | MPL notices | yes in principle | not downloaded | Not adopted |
| onnxruntime | 1.30.0 in venv | Runs Piper and Kokoro, already used by the project | https://github.com/microsoft/onnxruntime | MIT | Keep notice | yes | already installed | Already used |

## 3. LiveKit

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| livekit (python rtc) | 1.1.20 | Room, audio tracks, data channel | https://github.com/livekit/python-sdks | Apache-2.0 | Keep NOTICE and licence | yes | pip, already in venv | Adopted |
| livekit-api | 1.2.1 | Tokens, dispatch | https://github.com/livekit/python-sdks | Apache-2.0 | as above | yes | pip, already in venv | Adopted |
| livekit-agents | 1.8.4 | Worker, job dispatch | https://github.com/livekit/agents | Apache-2.0 | as above | yes | pip, already in venv | Adopted |
| livekit-client (JS) | 2.22.3 | Browser connection, microphone, remote audio, data | https://github.com/livekit/client-sdk-js | Apache-2.0 | as above | yes | jsDelivr `livekit-client@2.22.3/dist/livekit-client.umd.min.js` or vendored | Adopted |
| livekit-server | v1.13.7 | Local development server, `livekit-server --dev` | https://github.com/livekit/livekit | Apache-2.0 | as above | yes | `https://github.com/livekit/livekit/releases/download/v1.13.7/livekit_1.13.7_windows_amd64.zip` (18.9 MB), not committed | Adopted for development and tests |
| LiveKit dev keys `devkey` / `secret` | n/a | Local development only | https://github.com/livekit/livekit#running-livekit | placeholder values built into `--dev` | n/a | never use in production | built in | Development only |

## 4. Already used dependencies that matter to the new work

| Name | Why it matters | Licence |
|-|-|-|
| faster-whisper, Ollama, pyttsx3, FastAPI | unchanged by Part 2, listed in earlier documents | see requirements.txt |
| soundfile, numpy, scipy, sounddevice | audio handling in voicekit, already installed in the venv | BSD or MIT family, not re-verified here |

## 5. Attribution text to ship

No component adopted by default requires attribution except keeping licence notices for MIT, Apache-2.0 and LGPL code. A `licenses/` folder with the MIT, Apache-2.0 and CC0 texts and an about box line such as "3D avatar: MPFB and MakeHuman assets (CC0), TalkingHead (MIT), three.js (MIT)" is recommended. If a CC BY Piper voice is ever used, the dataset credit must be added there.

## Hosted service

| Service | Used for | Source | Terms | Notes |
| --- | --- | --- | --- | --- |
| LiveKit Inference (LiveKit Cloud model gateway, `agent-gateway.livekit.cloud`) | Writing the assistant's replies when `CLOUD_LLM_PROVIDER=livekit`; forwards to model vendors such as OpenAI | https://docs.livekit.io/agents/models/ | Part of the LiveKit Cloud service, billed to the project (credits or plan); the models' own vendor terms apply | Called with the project's own API key and secret. Not open source. Local Ollama answers when it is unavailable |

## Composition of `male.glb`

| Component | Source | Licence | In the model |
| --- | --- | --- | --- |
| MPFB 2.0.17 add-on code | https://extensions.blender.org/add-ons/mpfb/ | GPL-3.0-or-later code; the GPL does not extend to the model it exports | No, build tool only (installed in the Blender user profile) |
| MakeHuman system assets, CC0 variant (basemesh, young caucasian male skin and texture, low poly eyes and material, eyebrow001, eyelashes01, teeth_base, tongue01, short02 hair) | https://files.makehumancommunity.org/asset_packs/makehuman_system_assets/makehuman_system_assets_cc0.zip | CC0 (the pack is the community's `_cc0` variant) | Yes |
| Visemes 02 (Meta viseme targets) | https://files.makehumancommunity.org/functional/visemes02.zip | CC0 per the MakeHuman asset pack page; NOT confirmed inside the zip | Yes. TO CONFIRM before shipping |
| Faceunits 01 (ARKit face unit targets) | https://files.makehumancommunity.org/functional/faceunits01.zip | CC0 per the MakeHuman asset pack page; NOT confirmed inside the zip | Yes. TO CONFIRM before shipping |
| TalkingHead rig `talkinghead.mpfbskel` and weights `talkinghead.mhw` | https://github.com/met4citizen/TalkingHead/tree/main/blender/MPFB | MIT | Yes, as skeleton and skin weights |
| `talkinghead-addon.py` (scale and axis helpers) | same repository | MIT | No, build tool only |
| `clothes/male_casualsuit04` (short sleeve crew neck T-shirt with jeans; mesh, UVs, weights fitted by MPFB) | `clothes/male_casualsuit04` in the same CC0 system assets pack | CC0, explicitly released in 2020 per the asset file header | Yes |
| T-shirt textures (`male_casualsuit04_diffuse.png`, normal and AO maps) | same pack | CC0. The build script paints the logo, trim and watermark out of the diffuse map in memory (nothing is written to the pack) and downscales the maps to 1024 px | Yes |
| Blender 5.2.2 LTS (glTF exporter) | https://www.blender.org | GPL-3.0; exported output belongs to its author | No, build tool only |

The optional non system MPFB asset packs were not used. The model wears a T-shirt
and jeans, is barefoot, has a static hair mesh and is not compressed (no Draco or
Meshopt).


## Icons for the UI layer (2026-10-12)

| Asset | Source | Licence | Shipped |
|---|---|---|---|
| 28 icons in `frontend/ui/icons/sprite.svg` (mic, mic-off, users, user, id-card, shield-check, message-square, mail, rocket, key-round, timer, circle-check, triangle-alert, clock-alert, phone-call, settings, server, plug, history, log-out, captions, x, ellipsis, sun, moon, chevron-left, chevron-right, wifi-off) | lucide-static 1.55.0 from npm, https://lucide.dev, only the used icons copied | ISC. Seven of them (log-out, chevron-left, chevron-right, moon, x, server, ellipsis) derive from Feather and are MIT | Yes, inline in `icons.js` |

The app icons in `frontend/assets/icons/` were cut from the existing
`mobitel-logo.png` (no logo was downloaded). No animation library or font was
added; Noto Sans Sinhala and Noto Sans Tamil are loaded from Google Fonts (SIL
Open Font Licence 1.1).

Lucide licence:

    ISC License
    Copyright (c) 2026 Lucide Icons and Contributors
    Permission to use, copy, modify, and/or distribute this software for any
    purpose with or without fee is hereby granted, provided that the above
    copyright notice and this permission notice appear in all copies.
    THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
    WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
    MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
    ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
    WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
    ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
    OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

Feather licence (for the seven derived icons):

    MIT License
    Copyright (c) 2013-present Cole Bemis
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:
    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

## SLT site icons (2026-10-13)

| Asset | Source | Licence | Shipped |
|---|---|---|---|
| `frontend/favicon.ico` (48 px), `frontend/assets/icons/slt-favicon-96.png`, `frontend/assets/icons/slt-apple-touch-icon.png` (180 px) | https://www.slt.lk/favicon.ico, /favicon-96x96.png and /apple-touch-icon.png, the icons that slt.lk itself declares in its page head, downloaded 2026-10-13 | SLT's own logo mark (a trademark of the company), used on SLT's own assistant; not a free licence for anyone else | Yes |

The `favicon.svg` on slt.lk was not used: it is a bitmap wrapped in SVG, not a vector.
The web app manifest icons (`icon-192.png`, `icon-512.png`, `icon-512-maskable.png`) are
still cut from the logo already in the repository.


## Gemini Live, voices, localization and avatar additions (2026-10-15)

Rows written by the agents that did the work; the lead removed nothing except where noted. Nothing here has had legal review.

### Voices and speech

Add these rows to the table of the voice and speech section (the one with columns Name, Version, Used for, Source, Licence,
Attribution required, Commercial use OK, How obtained, Status), after the `dialoglk/SinhalaVITS-TTS-*` row. Checked on 2026-10-15.
Nothing marked Rejected may be a default; none of the rows below is.

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| Gemini prebuilt voices (Kore, Aoede, Puck, Charon) | service side, models `gemini-3.8-live`, `gemini-3.8-flash-tts` | Tier 1 voices, optional `gemini_tts` provider | https://ai.google.dev/gemini-api/docs/speech-generation | Google Gemini API terms (a service; no per voice licence) | per Google terms | yes under the paid API terms, UNVERIFIED here (key has no credit) | Network call at runtime | Adopted as tier 1, never heard |
| LiveKit Inference TTS (Inworld TTS 2.0 and 2.0 Flash, Google Gemini 3.8 Flash and Flash-Lite TTS) | model ids `inworld/inworld-tts-2`, `inworld/inworld-tts-2-flash`, `google/gemini-3.8-flash-tts`, `google/gemini-3.8-flash-lite-tts` | Tier 2 voices (Sinhala only exists on Inworld 2.0) | https://docs.livekit.io/agents/models/tts/ , https://docs.livekit.io/agents/models/tts/inworld/ , https://docs.livekit.io/agents/models/tts/gemini/ | LiveKit Cloud terms plus the model vendors' terms; billed to the LiveKit project; prices not stated on the pages read | none known | per plan, UNVERIFIED | Network call at runtime with the project's own key | Adopted as tier 2, never run |
| Inworld voices Ashley, Edward (and Diego, Olivia) | service side | Tier 2 Sinhala and Tamil voice names (Sinhala fit unknown) | https://docs.livekit.io/agents/models/tts/inworld/ | Inworld terms through LiveKit Inference | none known | per plan, UNVERIFIED | Network call | Adopted for trial, voice quality unknown |
| Edge voices `en-US-AvaMultilingualNeural`, `en-US-AndrewMultilingualNeural` | service side | English voices (first choice, unchanged) | Microsoft Edge read aloud service | Microsoft service terms, UNVERIFIED for commercial use | none known | UNVERIFIED | Network call | Adopted, first choice as before (Aria and Guy are the listed alternates; the measured latency gap was small) |
| Edge voices `ta-IN-PallaviNeural`, `ta-IN-ValluvarNeural` | service side | Tamil fallback voices | same | same | none known | UNVERIFIED | Network call | Adopted as fallback (already listed above) |
| Piper `si_LK-dilu-medium` | not found | Optional offline Sinhala female | https://huggingface.co/rhasspy/piper-voices (no `si` directory on 2026-10-15) | Claimed MIT in the brief; no model card could be read | unknown | NO until the model card is read | Not downloaded | Not adopted; adapter `providers/piper_si.py` stays unavailable without a hand placed model in `data/models/piper_si/` |
| `backend/voice_text/lexicon.yaml` spellings | n/a | Sinhala and Tamil respelling of English telecom words and abbreviations | written in this project | project licence | none | yes | in repository | In use, spellings not checked by a native speaker |
| PyYAML | 6.0.3 in venv, already in requirements.txt | Reads `config/voices.yaml` and `lexicon.yaml` | https://github.com/yaml/pyyaml | MIT | Keep notice | yes | pip | Already used |

Rejected (add if absent, all already in the file or the evaluation): MMS-TTS Sinhala and Tamil (CC BY-NC 4.0, non commercial),
`intellisr/sinhala-tts-piper-v1` (tagged GPL-3.0, data rights unclear), SinhalaVITS (MPL-2.0, heavy, competitor operator).

### Avatar

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| `male_sl.glb`, `female_sl.glb` (Sri Lankan SLT avatars, built here) | 3,648,136 bytes, SHA-256 `5ccd6d746a61bfe1fe1be32219ad951e7ba8342ee5e9989824d78eae8e952027` (male) and 7,371,508 bytes, SHA-256 `430334836ef4a1b4fb7c3ffc08ec8be30621a9f82aa0aa8ac1832e8db886b877` (female), medium_brown preset | Default avatars with a black SLT T-shirt | Derived from `male.glb` and `mpfb.glb` by `tools/avatar/build_avatar.py` | CC0 sources (recolored textures, same geometry); the logo is SLT's own file | none for the CC0 parts; the logo stays SLT's mark | yes (own logo) | Built with Blender, stored with Git LFS | Adopted, legacy `male.glb` and `mpfb.glb` stay as fallback |
| SLT Mobitel logo `frontend/assets/mobitel-logo.png` | repo file, 3840 x 1451 RGBA | Chest decal baked into the shirt texture, unmodified in shape and colour | this repository | Company trademark | n/a | internal use | already in repo | Adopted |
| Blender | 5.2.2 LTS | Runs `build_avatar.py` (bpy, glTF importer and exporter) | https://www.blender.org | GPL-3.0, output is the author's | none | yes for output | Installed | Build tool, not shipped |
| meshoptimizer (EXT_meshopt_compression, inside Blender's exporter) | bundled with Blender 5.2 | Shrinks `female_sl.glb` from about 9.3 MB to 7.4 MB | https://github.com/zeux/meshoptimizer | MIT | keep notice if redistributed | yes | bundled | Used for `female_sl.glb`; the decoder is already vendored (`vendor/three/examples/jsm/libs/meshopt_decoder.module.js`) and TalkingHead registers it |

### Sinhala speech to text model

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| `janiduchamika/faster-whisper-small-sinhala-ct2-float16` | 489 MB, CTranslate2 float16, run as int8 on the CPU | Speech to text for Sinhala (final transcripts only) | https://huggingface.co/janiduchamika/faster-whisper-small-sinhala-ct2-float16 | NOT STATED on the model page. It is a fine tune of OpenAI Whisper small (MIT); the licence of the Sinhala training data is not stated | unknown | UNVERIFIED | Downloaded once in the background at start up into `data/models/whisper-sinhala` (not in git) when `STT_SINHALA_AUTO_DOWNLOAD=true` | Adopted as the Sinhala default because the stock models cannot read Sinhala (match 0.09 against 0.87 on short test questions). Needs a licence check before any commercial use; `STT_SINHALA_AUTO_DOWNLOAD=false` removes it |
| Other Apache 2.0 Sinhala Whisper fine tunes (`AqeelShafy7/Whisper-Sinhala_Audio_to_Text`, `alphaedge-ai/whisper-medium-sin-32768`, `Ransaka/whisper-tiny-sinhala-20k`) | n/a | Alternatives with a stated licence | https://huggingface.co/AqeelShafy7/Whisper-Sinhala_Audio_to_Text | Apache-2.0 | keep notice | yes | not downloaded; they are in Transformers format and need converting to CTranslate2, which needs torch (not installed here) | Not adopted, not tested |

### Fonts

Written 2026-10-15 by Agent D. Add these rows to a new section "Fonts" in `docs/THIRD_PARTY.md`, using the same columns as the avatar table. Nothing here has had legal review. Licence facts below are from the font projects' published licence (SIL Open Font License 1.1); confirm against the licence file inside the downloaded font package before vendoring.

| Name | Version | Used for | Source | Licence | Attribution required | Commercial use OK | How obtained | Status |
|-|-|-|-|-|-|-|-|-|
| Noto Sans Sinhala | current Google Fonts release | Sinhala text on every screen | https://fonts.google.com/noto/specimen/Noto+Sans+Sinhala , https://github.com/notofonts/sinhala | SIL Open Font License 1.1 | Keep the copyright notice and the licence text with any copy of the font files; the font may not be sold on its own | yes | Loaded today from the Google Fonts stylesheet in `frontend/index.html` (weights 400, 600, 700). Optional: vendor the woff2 files into `frontend/assets/fonts/` | Adopted (hosted). Vendoring optional |
| Noto Sans Tamil | current Google Fonts release | Tamil text on every screen | https://fonts.google.com/noto/specimen/Noto+Sans+Tamil , https://github.com/notofonts/tamil | SIL Open Font License 1.1 | same as above | yes | same as above | Adopted (hosted). Vendoring optional |
| Google Fonts service | n/a | Delivers the two families above | https://fonts.googleapis.com , https://fonts.gstatic.com | Terms of the Google Fonts API; the fonts themselves are OFL | none | yes | A stylesheet link, no key | Adopted. Privacy note: the visitor's browser contacts Google to fetch the fonts, which sends the visitor's IP address; vendoring the files removes that request |

If the files are vendored: copy each family's `OFL.txt` next to the woff2 files (`frontend/assets/fonts/OFL-NotoSansSinhala.txt`, `frontend/assets/fonts/OFL-NotoSansTamil.txt`), subset only to the Sinhala (U+0D80 to U+0DFF) or Tamil (U+0B80 to U+0BFF) blocks plus the joiners U+200C and U+200D (subsetting is allowed by the OFL as long as the font is not renamed in a way the licence forbids; keep the Reserved Font Name rules in mind and keep the original family name only if no Reserved Font Name is declared in the package).
