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
