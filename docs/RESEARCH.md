# Research for Part 2: avatar, assets, lip sync, voices, LiveKit

Written 2026-10-05 by Agent A. Every claim carries a link. Where I ran something myself on this machine (Windows 11, Python 3.12 venv, 20 logical CPUs) it says "measured here". Anything I could not verify is marked "UNVERIFIED".

Contents: 1 Avatar renderer, 2 Assets, 3 Lip sync for Sinhala and Tamil, 4 Voices, 5 LiveKit, 6 Decisions needed.

## Summary of recommendations

1. Renderer: met4citizen/TalkingHead (MIT), pinned to a main branch commit, not the v1.7.0 tag. Audio driven visemes from HeadAudio (MIT). Three.js 0.180.0 through an import map.
2. Female avatar: `mpfb.glb` from the TalkingHead repo, CC0, 36.8 MB, ARKit plus Oculus blend shapes verified by me inside the file.
3. Male avatar: no ready made file with a safe licence exists. Least bad route is to build one with MPFB in Blender (CC0 assets, Blender 5.2.2 is already installed on this machine). Until then the female model is the only qualifying asset.
4. Sinhala and Tamil lip sync: no viseme model exists. Use HeadAudio (language independent audio analysis) plus an energy driven jaw floor. edge-tts word boundaries are real for both languages (measured) but only help subtitles and timing, not mouth shapes.
5. Voices: keep edge-tts for English, Sinhala and Tamil (voice names verified live today). Local English fallback: Piper voices `en_US-john-medium` (male) and `en_US-kristin-medium` (female), both public domain data, real time factor 0.05 measured here. Piper itself is GPL-3.0, which is a licence decision for the owner (section 4.4).
6. LiveKit: livekit 1.1.20, livekit-agents 1.8.4, livekit-api 1.2.1 are already installed in the project venv. The `Worker` class named in the plan no longer exists in agents 1.8; the equivalent is `AgentServer` with the `WorkerOptions` alias. I ran a local `livekit-server --dev` 1.13.7 and verified audio in both directions, `clear_queue` and `publish_data` with a topic.

## 1. Avatar renderer

### 1.1 Options compared

| Option | Licence | Lip sync input | Ready for our need | Verdict |
|-|-|-|-|-|
| [TalkingHead](https://github.com/met4citizen/TalkingHead) | MIT, [LICENSE](https://raw.githubusercontent.com/met4citizen/TalkingHead/main/LICENSE) | Word timings, Oculus viseme ids, raw blend shape animation, or live audio via HeadAudio | Yes. Moods, gestures, blink, gaze, idle, camera views, streaming PCM | Recommended |
| [HeadAudio](https://github.com/met4citizen/HeadAudio) | MIT | Any audio stream, no transcript | Works with TalkingHead, about 50 ms latency | Recommended companion |
| [three-vrm](https://github.com/pixiv/three-vrm) v3.5.5 | MIT | None built in, you write the viseme driver | VRM expression presets only (aa, ih, ou, ee, oh), no ARKit set | Not chosen, more code, weaker visemes |
| [wawa-lipsync](https://github.com/wass08/wawa-lipsync) | MIT | Web Audio analysis of an `<audio>` element | A small library, no avatar, no moods or gestures, no releases | Possible simple fallback only |
| [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) v1.14.0 | MIT ([licence summary](https://raw.githubusercontent.com/DanielSWolf/rhubarb-lip-sync/master/LICENSE.md)) | Offline analysis of a wav file, English oriented | Not real time, runs server side, mouth shapes are 2D cartoon cues | Rejected for live use |
| Oculus Lipsync ports | Oculus licence, not open | n/a | Not a clear commercial licence, not verified | Rejected |
| Ready Player Me | n/a | n/a | Shut down 31 January 2026 ([report](https://avatarsdk.com/blog/2026/01/15/switch-from-ready-player-me-to-avatar-sdk-fast-familiar-production-ready/), [Variety](https://variety.com/2025/digital/news/netflix-acquires-ready-player-me-games-avatar-creation-1236612915/)). TalkingHead's own commit "Netflix buys Ready Player Me" is dated 2025-12-21 | No dependency allowed |

### 1.2 TalkingHead facts (verified from the repository and package files)

- Latest release tag is v1.7.0, published 2025-12-08 ([releases](https://github.com/met4citizen/TalkingHead/releases)); `package.json` says version 1.7.0 and depends on `three ^0.180.0` ([package.json](https://raw.githubusercontent.com/met4citizen/TalkingHead/main/package.json)). The npm package is [@met4citizen/talkinghead](https://www.npmjs.com/package/@met4citizen/talkinghead).
- Important: `main` is ahead of the tag. The CC0 `mpfb.glb` was added on 2026-01-15 and the skeleton retargeter (`modules/retargeter.mjs`), Meshopt support and the avatar level options were added after v1.7.0 ([commits](https://github.com/met4citizen/TalkingHead/commits/main)). I checked that `https://cdn.jsdelivr.net/gh/met4citizen/TalkingHead@1.7/modules/retargeter.mjs` returns 404 while the same path at the main commit returns 200, and that `avatars/mpfb.glb` does not exist at tag v1.7.0. So pin to the main commit `b3e277b3b46f88e557bf28a2c5612a5b04e075c3` (2026-09-25, adds a pt_br lip sync module), or move to a newer tag if one appears. This is not render tested by me.
- Loading: ES module through an import map. The README snippet is below. For production, vendor the modules (talkinghead.mjs 219,509 bytes, dynamicbones.mjs, retargeter.mjs, playback-worklet.js, lipsync modules) into `frontend/avatar/vendor/` instead of loading from a CDN, because the code loads `playback-worklet.js` through `new URL('./playback-worklet.js', import.meta.url)` and AudioWorklet modules fetched cross origin add a failure mode. The pinned jsDelivr URLs below returned HTTP 200 when I checked.
- Imports used inside talkinghead.mjs: `three`, `three/addons/controls/OrbitControls.js`, `GLTFLoader`, `DRACOLoader`, `FBXLoader`, `RoomEnvironment`, `stats.module.js`. The plan's risk 7 (global three@0.128 for bot3d.js) is handled by the import map keeping `three` as an ES module only inside the avatar page scope. Note an import map applies to the whole document, so if `bot3d.js` is loaded as a classic script with its own global THREE they do not clash, but `import map` must be declared before any module script.
- Lip sync language modules shipped: en, fi, lt, de, fr, pt_br ([modules](https://github.com/met4citizen/TalkingHead/tree/main/modules)). None for Sinhala or Tamil.
- Mixamo animation licence note from the README: Mixamo animations may be used royalty free commercially but raw animation files cannot be redistributed outside the project team. We do not need Mixamo files, the built in gestures are enough.

Import map (from the [README](https://github.com/met4citizen/TalkingHead#talkinghead-class), commit pinned by me):

```html
<script type="importmap">
{ "imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js/+esm",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/",
  "talkinghead": "https://cdn.jsdelivr.net/gh/met4citizen/TalkingHead@b3e277b3b46f88e557bf28a2c5612a5b04e075c3/modules/talkinghead.mjs"
}}
</script>
```

### 1.3 What audio and timing inputs it accepts

From the README method table and Appendices F and G:

- `speakAudio({audio, words, wtimes, wdurations, visemes, vtimes, vdurations, markers, mtimes, anim}, {lipsyncLang, isRaw}, onsubtitles)`. `audio` is an `AudioBuffer` or an array of 16 bit little endian PCM chunks (class option `pcmSampleRate`, default 22050). Times are milliseconds. If only words are given, a lip sync module for `lipsyncLang` turns words into visemes. If Oculus viseme ids are given they are used directly.
- `anim` accepts a blend shape animation template `{name:"blendshapes", dt:[...], vs:{jawOpen:[...]}}` played in sync with the audio, so any external source can drive the mouth.
- Streaming mode: `streamStart({sampleRate, lipsyncType:"visemes"|"blendshapes"|"words", lipsyncLang, gain, waitForAudioChunks, mood}, onAudioStart, onAudioEnd, onSubtitles)`, then `streamAudio({audio: Int16 PCM, visemes, vtimes, vdurations | words, wtimes, wdurations | anims})`, `streamNotifyEnd()`, `streamInterrupt()` (barge in, keeps the session), `streamStop()`. One stream at a time. This is the natural sink for voicekit `/ws/tts` PCM when LiveKit is not used.
- Direct control: `head.setFixedValue("jawOpen", 1)` or `Object.assign(head.mtAvatar["jawOpen"], {realtime: v, needsUpdate: true})`.
- HeadAudio: an AudioWorklet node. Language independence is by design (no transcript, MFCC features compared with Gaussian prototypes) but the only shipped model, `model-en-mixed.bin`, was trained on English HeadTTS output; the author says accuracy "is far from optimal" at low signal to noise ([README](https://github.com/met4citizen/HeadAudio#readme)). Custom models can be trained from about 10 minutes of audio with timings, which is out of scope for us now. End to end latency about 50 ms, so add a 50 to 100 ms delay on the audible path.
- README inconsistency: the HeadAudio example loads `./dist/model-en-mixed.mjs` but the repository file is `dist/model-en-mixed.bin` and `loadModel` just `fetch`es the URL, so use the `.bin` (verified in the source of `loadModel`).

### 1.4 Blend shapes the GLB must contain

From [Appendix A](https://github.com/met4citizen/TalkingHead#appendix-a-create-your-own-3d-avatar): a Mixamo compatible skeleton, root object named `Armature`, all 52 ARKit shapes and the 15 Oculus visemes (`viseme_sil, PP, FF, TH, DD, kk, CH, SS, nn, RR, aa, E, I, O, U`). `mouthOpen, mouthSmile, eyesClosed, eyesLookUp, eyesLookDown` are generated automatically if missing. Eyes, teeth and tongue are separate meshes with their own shape keys in the CC0 file.

### 1.5 Mood, gesture, idle, blink, gaze APIs

- Moods: `neutral, happy, angry, sad, fear, disgust, love, sleep` through `head.setMood(m)` or `avatarMood` option.
- Gestures: `head.playGesture(name, dur=3, mirror=false, ms=1000)` with `handup, index, ok, thumbup, thumbdown, side, shrug` plus emojis; `head.stopGesture()`; `head.speakWithHands()` is used in the HeadAudio example.
- Gaze: `lookAtCamera(ms)`, `lookAhead(ms)`, `lookAt(x,y,ms)`, `makeEyeContact(ms)`; options `avatarIdleEyeContact` (0.2), `avatarSpeakingEyeContact` (0.5), `avatarIdleHeadMove` (0.5), `avatarSpeakingHeadMove` (0.5), `avatarIgnoreCamera`.
- Blink and idle breathing are automatic while the animation loop runs. Views: `setView("full"|"mid"|"upper"|"head", {cameraDistance, cameraX, cameraY})`. `head.stop()` and `head.start()` pause and resume the loop (useful for hidden tabs). `modelFPS` defaults to 30.

### 1.6 Minimal usage code

```js
import { TalkingHead } from "talkinghead";

const head = new TalkingHead(document.getElementById("avatar"), {
  lipsyncModules: ["en"],        // only load what we use
  cameraView: "upper",
  modelFPS: 30,
  avatarMood: "neutral",
  mixerGainSpeech: 3,
});
await head.showAvatar({
  url: "/avatar/assets/mpfb.glb",
  body: "F",
  lipsyncLang: "en",
  // MPFB needs the retargeter and dynamic bones from siteconfig.js in the TalkingHead repo;
  // copy its "MPFB" entry (modelDynamicBones and retarget) rather than starting empty.
});

// English with provider word timings (voicekit /ws/tts "timings" message)
head.speakAudio({
  audio: audioBuffer,            // or [Int16 PCM chunk, ...]
  words: ["Hello", "world"], wtimes: [0, 480], wdurations: [450, 500],
}, { lipsyncLang: "en" });

// Any language, audio driven (HeadAudio), connected to the speech gain node
await head.audioCtx.audioWorklet.addModule("./vendor/headworklet.min.mjs");
const headaudio = new HeadAudio(head.audioCtx, {
  processorOptions: {}, parameterData: { vadGateActiveDb: -40, vadGateInactiveDb: -60 } });
await headaudio.loadModel("./vendor/model-en-mixed.bin");
head.audioSpeechGainNode.connect(headaudio);
headaudio.onvalue = (key, value) =>
  Object.assign(head.mtAvatar[key], { newvalue: value, needsUpdate: true });
head.opt.update = headaudio.update.bind(headaudio);

head.setMood("happy"); head.playGesture("handup", 2); head.lookAtCamera(1000);
```

HeadAudio files (pinned commit `d3af5f9ff86ab6b2b1913d411a4e1922ec101953`, all HTTP 200 on jsDelivr): `dist/headaudio.min.mjs` 6,443 bytes, `dist/headworklet.min.mjs` 13,237 bytes, `dist/model-en-mixed.bin` 14,352 bytes. HeadAudio has no tagged release.

Playing a LiveKit remote track through HeadAudio, UNVERIFIED pattern: create `head.audioCtx.createMediaStreamSource(new MediaStream([remoteTrack.mediaStreamTrack]))` and connect it to the `headaudio` node only (the `<audio>` element from `track.attach()` does the audible playback). Chromium is known to need the remote stream attached to a media element before a WebAudio source receives samples. Agent D must test this in a real browser.

## 2. Assets

### 2.1 Result

| Role | File | Licence | Size | Verdict |
|-|-|-|-|-|
| Female | `avatars/mpfb.glb` in TalkingHead | CC0 ([README licence note](https://github.com/met4citizen/TalkingHead#the-indexhtml-test-app), made with MPFB whose assets are CC0) | 36,815,920 bytes | Qualifies |
| Male | none found | n/a | n/a | No qualifying ready made file |

Female file details (measured here by parsing the GLB header): generator Khronos glTF Blender I/O 5.0.21, 76 nodes, meshes `base` (66 morph targets), eyes (`high-poly`), eyelashes, eyebrows, teeth, tongue, outfit `female_casualsuit01`, a ponytail, 7 PNG textures (about 19 MB of the 36.8 MB), no Draco or Meshopt extensions. The `base` mesh carries all ARKit shapes and 14 of the 15 Oculus visemes: `viseme_sil` is not present as a shape key (silence is the rest pose, so this should be fine, UNVERIFIED in a browser). Skeleton uses Mixamo style bone names (LeftUpLeg, LeftHandIndex1 and so on). SHA-256 of the file I downloaded: `63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c`.

Direct download, pinned to the commit that added it (use this, not the v1.7.0 tag, the tag does not contain it, and jsDelivr will refuse it because it exceeds the 20 MB per file limit of its GitHub mirror, which I inferred from the 404 on a smaller path and have not confirmed in their docs):

`https://raw.githubusercontent.com/met4citizen/TalkingHead/6318ff8c798e4a661fcd70c9373c4ab789c822bb/avatars/mpfb.glb`

A script can fetch it with plain `curl -L -o mpfb.glb <url>` and verify the SHA-256 above. Licence text: [MPFB LICENSE.md](https://raw.githubusercontent.com/makehumancommunity/mpfb2/master/LICENSE.md) (assets CC0, section C and D say output of MPFB carries no claim) and [LICENSE.ASSETS.md](https://raw.githubusercontent.com/makehumancommunity/mpfb2/master/LICENSE.ASSETS.md) (CC0 1.0 full text), and the MakeHuman [licence page](https://static.makehumancommunity.org/about/license.html) ("You do not need to pay anything, include any copyright notices nor give attribution"). Residual risk: the licence of the file rests on the TalkingHead author's statement that it is CC0 plus the MakeHuman system assets being CC0. The README does not name which clothes and hair assets were used, so a one line confirmation from the author is cheap insurance. Reduce the size with `gltf-transform optimize in.glb out.glb --compress meshopt --texture-compress webp` (TalkingHead supports Meshopt by default, [README](https://github.com/met4citizen/TalkingHead#appendix-a-create-your-own-3d-avatar)); I did not run it.

### 2.2 Other bundled avatars, all rejected

| File | Licence stated in README | Verdict |
|-|-|-|
| `brunette.glb`, `brunette-t.glb` | Ready Player Me, CC BY-NC 4.0 | Rejected, non commercial |
| `avaturn.glb` | Avaturn, non commercial; commercial needs notice and extra terms | Rejected |
| `avatarsdk.glb` | non commercial | Rejected |
| `vroid.glb` | non commercial | Rejected |

### 2.3 Male avatar, options

1. Build with MPFB (recommended). Steps are in [MPFB.md](https://raw.githubusercontent.com/met4citizen/TalkingHead/main/blender/MPFB/MPFB.md): install Blender, the MPFB extension (version 2.0.17 on the [extensions platform](https://extensions.blender.org/add-ons/mpfb/), supports Blender 4.2 and later with no upper limit, 45 MB), the "MakeHuman system assets" pack, the "Visemes 02" pack ([130,733 bytes](https://files.makehumancommunity.org/functional/visemes02.zip)) and "Faceunits 01" ([257,821 bytes](https://files.makehumancommunity.org/functional/faceunits01.zip)), plus TalkingHead's `talkinghead-addon.py`, `talkinghead.mpfbskel`, `talkinghead.mhw`. Create a male human, add the `talkinghead` rig, apply skin, eyes, teeth, tongue, hair and an outfit, export a copy with "Load meta-style visemes", "Load arkit-style visemes" and "Interpolate", rename root to `Armature`, export GLB. Licence: MakeHuman core assets CC0, MPFB code GPL-3.0 but output carries no claim ([LICENSE.md section D](https://raw.githubusercontent.com/makehumancommunity/mpfb2/master/LICENSE.md)). Blender 5.2.2 LTS is already installed at `C:\Program Files\Blender Foundation\Blender 5.2` (checked with `blender --version`). Compatibility of the TalkingHead add-on with Blender 5.2 is UNVERIFIED (the repository's own file was exported with Blender I/O 5.0.21, so Blender 5.x works for the author). Effort: a few hours of manual work in the Blender UI, not scriptable end to end by me today. Only clothes, hair and skin assets tagged CC0 in the MakeHuman asset library should be used; a CC BY asset needs attribution; CC BY-SA and CC BY-NC assets must be avoided.
2. Microsoft Rocketbox (MIT, [repository](https://github.com/microsoft/Microsoft-Rocketbox)). The facial versions include 15 visemes and ARKit shapes, but the skeleton must be removed and re-rigged through Adobe Mixamo, then bones and shape keys renamed with TalkingHead's [rename-rocketbox-shapekeys.py](https://raw.githubusercontent.com/met4citizen/TalkingHead/main/blender/rename-rocketbox-shapekeys.py). It needs a Mixamo account and a manual web step, the repository is archived, and the 2022 GitHub tree of one avatar I listed showed only an `Export` and `Textures` folder plus a PNG, so I could not confirm the file layout quickly. Not recommended.
3. VRoid Studio to VRM to GLB through TalkingHead's [VROID.md](https://github.com/met4citizen/TalkingHead/blob/main/blender/VRoid/VROID.md): no paid add-ons, produces ARKit and Oculus shape keys (the doc says some ARKit keys are not implemented), but the style is anime and the VRoid terms of use could not be fetched (the page returned 403 and then a script rendered page), so the commercial terms are UNVERIFIED. The bundled `vroid.glb` is marked non commercial. Not recommended for a company product.
4. Ready Player Me, Avaturn, AvatarSDK: rejected (shut down or non commercial).
5. Interim fallback if no male model is produced: show the female avatar for both voices and let the toggle change only the voice. This is honest but not what the brief asks for.

Outcome (2026-10-06): option 1 was automated rather than done by hand. `frontend/avatar/tools/make_male_avatar.py` runs in headless Blender 5.2 in about three minutes and produced an 18 MB `male.glb` with all 52 ARKit and 15 Oculus shapes (including `viseme_sil`, which the female file lacks). The TalkingHead add-on worked on Blender 5.2. Its input licences are listed in docs/THIRD_PARTY.md; two of them still need the licence file inside the zip to be read.

I found no CC0 or MIT male GLB with both ARKit and Oculus shape keys on the open web. The web searches I ran returned only the TalkingHead repository and tools that convert shape keys, and one search summary wrongly claimed a male MPFB file exists; I checked the repository file list and it does not.

## 3. Lip sync for Sinhala and Tamil

Facts:

- No Oculus viseme model or TalkingHead lip sync module exists for Sinhala or Tamil (modules list above). Azure's viseme events do not cover them either as far as the README says ("100+ languages" claim is general, UNVERIFIED for si and ta).
- edge-tts does return `WordBoundary` events for `si-LK` and `ta-IN`. Measured here with edge-tts 7.2.8: for `si-LK-ThiliniNeural` three boundaries (`ආයුබෝවන්` at 175 ms for 375 ms, `ඔබට` at 737 ms, `කෙසේද` at 1000 ms), for `ta-IN-PallaviNeural` four boundaries, for English five. First audio byte after 0.55 s (si), 1.17 s (ta) and 0.60 s (en). Note: edge-tts 7.x defaults to sentence boundaries; pass `boundary="WordBoundary"` ([edge-tts](https://github.com/rany2/edge-tts)).
- Word timings are not mouth shapes. Feeding Sinhala words into the English text to viseme module would give wrong shapes.

Recommendation, in order:

1. Primary for si and ta: HeadAudio on the audible stream (language independent in design, English trained model, quality UNVERIFIED for these languages, needs a Sinhala and Tamil speaker to judge it).
2. Floor that always works: energy driven jaw. Read an `AnalyserNode` RMS every frame and write `jawOpen` (0 to about 0.5) plus a little `mouthFunnel` and `mouthSmile` variation through `Object.assign(head.mtAvatar["jawOpen"], {realtime: v, needsUpdate: true})`. This is cheap, stable and reads as speech.
3. Use edge-tts word boundaries for subtitles, pause detection (`speakBreak`, gaze changes `lookAtCamera` at sentence starts) and for English `speakAudio` word timing, not for visemes in si and ta.
4. English can use provider word timings with the built in text module (best accuracy per the TalkingHead author) or HeadAudio.

To keep one code path, the avatar renderer can use HeadAudio for every language and add the text path only for English when timings exist.

## 4. Voices (R6)

### 4.1 edge-tts voice names (verified today against the live voice list endpoint, edge-tts 7.2.8)

| Language | Female | Male |
|-|-|-|
| Sinhala si-LK | `si-LK-ThiliniNeural` | `si-LK-SameeraNeural` |
| Tamil India ta-IN | `ta-IN-PallaviNeural` | `ta-IN-ValluvarNeural` |
| Tamil Sri Lanka ta-LK | `ta-LK-SaranyaNeural` | `ta-LK-KumarNeural` |
| Tamil Singapore ta-SG | `ta-SG-VenbaNeural` | `ta-SG-AnbuNeural` |
| Tamil Malaysia ta-MY | `ta-MY-KaniNeural` | `ta-MY-SuryaNeural` |
| English US en-US | `en-US-AriaNeural`, `en-US-JennyNeural`, `en-US-AvaNeural`, `en-US-EmmaNeural` | `en-US-GuyNeural`, `en-US-AndrewNeural`, `en-US-BrianNeural` |
| English UK en-GB | `en-GB-SoniaNeural` | `en-GB-RyanNeural` |

For Sri Lankan users `ta-LK` is the closest dialect. Output from edge-tts is MP3 at 24 kHz (decode before sending PCM). The service is the unofficial Edge read aloud endpoint; the library is LGPL-3.0 ([LICENSE](https://raw.githubusercontent.com/rany2/edge-tts/master/LICENSE), MIT for one file only), and Microsoft's terms for that endpoint in a commercial product are UNVERIFIED and a risk. The official equivalent with the same voice names is Azure AI Speech (paid, free tier exists, not verified here), which voicekit already allows through `azure_key`.

### 4.2 Local English models, measured here (20 logical CPUs, onnxruntime 1.30, Python 3.12, wheels only, no compiling)

| | Piper `en_US-john-medium` / `kristin-medium` | Kokoro v1.0 through kokoro-onnx 0.6.1 |
|-|-|-|
| Engine licence | piper-tts 1.8.0 is GPL-3.0-or-later ([PyPI](https://pypi.org/project/piper-tts/), [piper1-gpl](https://github.com/OHF-Voice/piper1-gpl)); the old [rhasspy/piper](https://github.com/rhasspy/piper) was MIT but is archived and read only | kokoro-onnx MIT, model Apache-2.0 ([README](https://github.com/thewh1teagle/kokoro-onnx#license), [model card](https://huggingface.co/hexgrad/Kokoro-82M)) |
| Phonemiser | espeak-ng built into the wheel, GPL-3.0 | `phonemizer` 3.4.0 (GPL-3.0) plus `espeakng-loader` 0.2.4 (MIT wrapper, bundles espeak-ng GPL-3.0); misaki is Apache-2.0 but its English pipeline also falls back to espeak |
| Voice licence | Dataset licence per voice (below). john and kristin: public domain LibriVox recordings ([john card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/john/medium/MODEL_CARD), [kristin card](https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/kristin/medium/MODEL_CARD)) | Voice packs inside Apache-2.0 release; voices trained on data described in the model card |
| Size | 63.5 MB onnx each plus 5 KB json | `kokoro-v1.0.onnx` 325.5 MB, `fp16` 163.5 MB, `int8` 114.1 MB, `voices-v1.0.bin` 28.2 MB ([release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)) |
| Sample rate | 22,050 Hz | 24,000 Hz |
| Speed, measured here | Real time factor 0.04 to 0.05 (0.26 s to make 5 s of audio); first audio chunk 0.13 to 0.20 s; first call 3.9 s (warm up) | fp32: RTF 0.26 to 0.30 (1.1 s for 4.3 s of audio) after a 2 s first call. int8: RTF 3.0 on this CPU, slower than real time, do not use int8 here. One call returns the whole sentence, no streaming chunks |
| Word timings | Piper 1.8 can return per phoneme sample counts when the `onnx` extra is installed (`AudioChunk.phoneme_alignments`, `include_alignments=True`), UNTESTED by me | None from kokoro-onnx; the pytorch `kokoro` package can produce token timestamps, not tested; derive word times by proportional split of the audio duration |
| pip packages | `piper-tts` (pulls `onnxruntime`, `pathvalidate`); optional `onnx` for alignments. Windows wheel `cp39-abi3-win_amd64` 34 MB | `kokoro-onnx`, `espeakng-loader`, `phonemizer`, `numpy`, `onnxruntime`, `soundfile` for wav output. Python must be below 3.14 for the current release |
| Windows without compiling | Yes, installed and ran in a fresh venv. The wheel contains a compiled `espeakbridge.pyd` and it loaded here, so the Application Control policy did not block it in my test; your note says it may block compiled extensions, so retest in the real venv | Yes, wheels only, ran here (espeakng-loader ships a DLL) |
| Languages | English voices plus many others; no Sinhala or Tamil in the official voice repository (I filtered the file list for `si/` and `ta/`, only `hi/` exists) | English, Spanish, French, Hindi, Italian, Japanese, Mandarin, Brazilian Portuguese (per README voices); no Sinhala or Tamil |

Piper voice dataset licences I read from the model cards in [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) (repository card says MIT, but each voice has its own dataset licence):

| Voice | Gender | Dataset licence | OK for company product |
|-|-|-|-|
| `en_US-john-medium` | male | public domain (LibriVox) | yes |
| `en_US-bryce-medium` | male | public domain | yes |
| `en_US-norman-medium` | male | public domain | yes |
| `en_US-joe-medium` | male | CC0 | yes |
| `en_US-kristin-medium` | female | public domain | yes |
| `en_US-kathleen-low` | female | CC0 | yes, low quality |
| `en_US-libritts_r-medium` | 904 speakers | CC BY 4.0 | yes with attribution |
| `en_GB-alba-medium` | female | CC BY 4.0 | yes with attribution |
| `en_US-ryan-medium`, `en_US-hfc_female-medium`, `en_US-hfc_male-medium` | mixed | CC BY-NC-SA 4.0 | no |
| `en_GB-northern_english_male-medium`, `en_GB-southern_english_female-low` | mixed | CC BY-SA 4.0 | no, share alike |
| `en_US-lessac-medium` | female | Blizzard 2013 licence, special terms | avoid, not clearly commercial |
| `en_US-amy-medium`, `en_GB-alan-medium`, `en_GB-jenny_dioco-medium` | mixed | "See URL", not resolved by me | avoid until read |

Voice file URLs follow `https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/john/medium/en_US-john-medium.onnx` (and `.onnx.json`); john and kristin downloaded in a few seconds by `curl -L`.

GPL implication, plainly: both local engines depend on espeak-ng (GPL-3.0-or-later). Piper's own Python package is GPL-3.0 too. GPL obligations (offering source of the GPL parts and keeping the licence) arise when software is conveyed to someone else. Running it inside SLT's own servers without handing the program to third parties does not convey it, but our code would import a GPL library into the same process, and if the product is ever delivered to a customer the whole combination becomes a GPL question. Lower risk way: run the local TTS as a separate process or local HTTP service with its own licence notice, and keep it optional ("loaded only if its files are present", as the plan says). Whether that is acceptable for SLT is a legal decision, listed in section 6. I am not giving legal advice.

Recommendation: Piper with `en_US-john-medium` (male) and `en_US-kristin-medium` (female) as the optional local provider, because it is 5 times faster than Kokoro here, small, and its voices have clean data licences. Kokoro sounds more natural but is 325 MB, 5 to 6 times slower here and has the same GPL phonemiser issue, so it is the second choice. KittenTTS ([repository](https://github.com/KittenML/KittenTTS), Apache-2.0, model `KittenML/kitten-tts-nano-0.8` Apache-2.0) is a small English ONNX model I did not test; its phonemiser dependency was not checked.

### 4.3 Sinhala and Tamil local models

| Candidate | Licence | Verdict |
|-|-|-|
| `facebook/mms-tts-tam` and Sinhala MMS | CC BY-NC 4.0 ([card](https://huggingface.co/facebook/mms-tts-tam)) | Rejected |
| `ai4bharat/indic-parler-tts` | Apache-2.0 ([card](https://huggingface.co/ai4bharat/indic-parler-tts)) | Tamil yes, Sinhala not listed. A roughly 0.9B parameter model, too heavy for CPU real time, I did not run it. Not adopted |
| `ai4bharat/IndicF5` | MIT | Tamil yes, Sinhala no, large, not adopted |
| `intellisr/sinhala-tts-piper-v1` | model tagged GPL-3.0, dataset from Access to Success Organization with a named speaker, rights "may impose additional terms" ([card](https://huggingface.co/intellisr/sinhala-tts-piper-v1)) | Technically works: 20.6 MB onnx, 16 kHz, real time factor 0.03 to 0.04 measured here with piper-tts 1.8.0. I cannot judge the audio quality. Data rights are unclear, so not adopted without written permission from the authors |
| `dialoglk/SinhalaVITS-TTS-M1/M2/F1/F2` | MPL-2.0, Coqui VITS `.pth`, custom recorded dataset by Dialog Axiata and a university research lab ([card](https://huggingface.co/dialoglk/SinhalaVITS-TTS-M1)) | Needs torch and Coqui TTS (not onnx). Dialog is a competing telecom operator, so the owner may not want this even though the licence is permissive. Not adopted |
| Other community Sinhala and Tamil repos (MIT or Apache tags on fine tunes) | unclear data provenance | Rejected |

Conclusion: no vetted, openly licensed, CPU friendly Sinhala or Tamil model exists. Keep edge-tts (then Google, then SAPI) for si and ta. If the owner wants offline Sinhala, the best path is to ask the intellisr authors for written permission or record SLT's own voice data.

### 4.4 Chain for voicekit

cloud (existing Google/Azure) then edge-tts then local Piper for English only then SAPI5, as in ADR 2.8. Piper returns raw 16 bit PCM at 22,050 Hz; voicekit resamples as needed.

## 5. LiveKit patterns

Versions verified by PyPI and GitHub today, all Apache-2.0: [livekit](https://pypi.org/project/livekit/) python rtc 1.1.20 (Windows wheel 11 MB), [livekit-api](https://pypi.org/project/livekit-api/) 1.2.1, [livekit-agents](https://pypi.org/project/livekit-agents/) 1.8.4 (released 2026-10-01), [livekit-client](https://www.npmjs.com/package/livekit-client) 2.22.3 (JS, Apache-2.0), [livekit-server](https://github.com/livekit/livekit) v1.13.7 (2026-09-14, Apache-2.0). The project venv already has livekit 1.1.20, livekit-agents 1.8.4 and livekit-api 1.2.1 installed.

Important differences from the plan wording:

- `livekit.agents` 1.8.4 has no `Worker` class. The public names are `AgentServer` and `WorkerOptions` (an alias of `ServerOptions`), `JobContext`, `JobRequest`, `JobExecutorType`. Source: `livekit/agents/worker.py` in the installed package (`WorkerOptions = ServerOptions`, `class AgentServer`).
- On Windows the default `job_executor_type` is already `JobExecutorType.THREAD` (`worker.py`: `if sys.platform.startswith("win"): _default_job_executor_type = JobExecutorType.THREAD`). Set it explicitly anyway.
- `AgentServer.rtc_session` accepts only one entrypoint and takes `agent_name`; a non empty `agent_name` turns off automatic dispatch so the agent joins only on explicit dispatch (docstring in `worker.py`: "To use explicit dispatch, your agent must be registered with an agentName", see also [docs](https://docs.livekit.io/agents/server/agent-dispatch/), UNVERIFIED page address).
- Running a worker inside the existing process: `AgentServer.run(devmode=False)` is an `async` method; `livekit.agents.cli.run_app(server)` is documented in the source as "deprecated". The worker source has no `signal` handling of its own, so running `await server.run()` in a task or thread should be possible, but I did not run it inside a non main thread. UNVERIFIED, test it first. The job executor setting as thread keeps one Whisper copy (plan risk 5).

### 5.1 Server side verified snippets (against installed source, and run against a local server)

Token with explicit dispatch (verified to produce a JWT with livekit-api 1.2.1):

```python
import datetime
from livekit import api

token = (api.AccessToken(API_KEY, API_SECRET)
    .with_identity("user-123")
    .with_ttl(datetime.timedelta(minutes=10))
    .with_grants(api.VideoGrants(room_join=True, room="room-1",
                                 can_publish=True, can_subscribe=True, can_publish_data=True))
    .with_room_config(api.RoomConfiguration(
        agents=[api.RoomAgentDispatch(agent_name="voice-agent")]))   # dispatch on join
    .to_jwt())

# or dispatch explicitly from the server
async with api.LiveKitAPI(url, API_KEY, API_SECRET) as lk:
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(agent_name="voice-agent", room="room-1", metadata="{}"))
```

Worker entrypoint (names verified by import; the body is the pattern, not a full session):

```python
from livekit import rtc
from livekit.agents import AgentServer, JobContext, JobExecutorType

server = AgentServer(job_executor_type=JobExecutorType.THREAD,
                     ws_url=LIVEKIT_URL, api_key=API_KEY, api_secret=API_SECRET,
                     num_idle_processes=0, port=0)

@server.rtc_session(agent_name="voice-agent")
async def entrypoint(ctx: JobContext):
    await ctx.connect()              # joins ctx.room, auto subscribes to audio by default
    room = ctx.room                  # an rtc.Room
    ...                              # LiveKitVoiceSession(room).run()

# in an async context:  await server.run(devmode=False)
# or as a script:       from livekit.agents import cli; cli.run_app(server)
```

`JobContext.connect`, `.room`, `.api`, `.wait_for_participant`, `.add_shutdown_callback`, `.shutdown` exist (`job.py`). `WorkerOptions(entrypoint_fnc=..., agent_name=..., job_executor_type=...)` is the equivalent dataclass form.

Raw rtc usage, all run by me against `livekit-server --dev` 1.13.7 (two participants in one room):

```python
from livekit import rtc

room = rtc.Room()

@room.on("track_subscribed")
def on_track(track, publication, participant):
    if track.kind == rtc.TrackKind.KIND_AUDIO:
        asyncio.create_task(consume(track))

async def consume(track):
    # LiveKit resamples and downmixes for you
    async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
        pcm = ev.frame.data            # int16 samples, ev.frame.samples_per_channel
        ...

@room.on("data_received")
def on_data(p: rtc.DataPacket):        # one argument: data, kind, participant, topic
    if p.topic == "va-control": ...

await room.connect(URL, token)
src = rtc.AudioSource(24000, 1)                        # sample_rate, num_channels, queue_size_ms=1000
track = rtc.LocalAudioTrack.create_audio_track("agent-voice", src)
await room.local_participant.publish_track(
    track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

frame = rtc.AudioFrame(pcm_bytes, 24000, 1, samples_per_channel)   # 10 ms frames are typical
await src.capture_frame(frame)                         # waits when the queue is full
src.clear_queue()                                      # barge in: drops queued audio now
await room.local_participant.publish_data(b'{"type":"interrupt"}',
                                          reliable=True, topic="va-events")
```

Results of my run: user side sent a 1 s tone at 48 kHz and the agent received it as 16 kHz mono (33,440 samples counted, includes trailing silence frames); the agent published 1 s of tone at 24 kHz and the user received 46,320 samples at 24 kHz; `queued_duration` was 0.921 s before `clear_queue()` and 0.0 after; a data message with topic `va-control` arrived with the topic intact. A harmless `AssertionError` is printed from `FfiHandle.__del__` at interpreter exit in the script; disconnect and close before exit in real code. `rtc.AudioResampler(input_rate, output_rate, num_channels=1)` with `push()` and `flush()` exists if you resample yourself. `rtc.RoomOptions(auto_subscribe=True, ...)` is the connect option.

Local server on Windows, verified: download `https://github.com/livekit/livekit/releases/download/v1.13.7/livekit_1.13.7_windows_amd64.zip` (18.9 MB, contains `livekit-server.exe` and `LICENSE`), unzip, run `livekit-server.exe --dev`. It started without being blocked, logged "using placeholder keys", API key `devkey`, secret `secret` ([README](https://github.com/livekit/livekit#running-livekit)), listens on `ws://127.0.0.1:7880`. It logs an error "CPU monitoring unsupported on current platform", which is harmless (capacity management is off). The dev secret is only 6 bytes, so PyJWT prints an `InsecureKeyLengthWarning`; ignore it in development.

### 5.2 Browser (livekit-client 2.22.3)

CDN (HTTP 200 checked, 594 KB UMD): `https://cdn.jsdelivr.net/npm/livekit-client@2.22.3/dist/livekit-client.umd.min.js`, global `LivekitClient`. The README itself references the unpinned form ([README](https://github.com/livekit/client-sdk-js)); pin the version. Signatures below were read from the published type files ([LocalParticipant.d.ts](https://unpkg.com/livekit-client@2.22.3/dist/src/room/participant/LocalParticipant.d.ts), [events.d.ts](https://unpkg.com/livekit-client@2.22.3/dist/src/room/events.d.ts), [track/options.d.ts](https://unpkg.com/livekit-client@2.22.3/dist/src/room/track/options.d.ts)):

```js
const { Room, RoomEvent, Track } = LivekitClient;
const room = new Room({
  audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
});

room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
  if (track.kind === Track.Kind.Audio) {
    const el = track.attach();            // creates an <audio> element
    document.body.appendChild(el);
  }
});
room.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
  if (topic === "va-events") handle(JSON.parse(new TextDecoder().decode(payload)));
});
room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
  if (!room.canPlaybackAudio) showTapToStartButton();   // button click then: await room.startAudio();
});

await room.connect(url, token);
await room.localParticipant.setMicrophoneEnabled(true, { echoCancellation: true, noiseSuppression: true });
// setMicrophoneEnabled(enabled, options?: AudioCaptureOptions, publishOptions?: TrackPublishOptions)
await room.localParticipant.publishData(
  new TextEncoder().encode(JSON.stringify({ type: "mute", muted: true })),
  { reliable: true, topic: "va-control" });
```

`AudioCaptureOptions` has `echoCancellation`, `noiseSuppression`, `autoGainControl`, `channelCount`, `deviceId`, `latency`, `voiceIsolation`, `restrictOwnAudio`. Browsers may block audio autoplay until a click, hence `AudioPlaybackStatusChanged` and `room.startAudio()` ([README](https://github.com/livekit/client-sdk-js#handling-common-track-types)). Echo cancellation matters here because the assistant's voice plays on the same laptop as the microphone, and barge in depends on it.

## 6. Decisions needed

1. Male avatar. Authorise building a CC0 male with MPFB in Blender (a few hours, manual UI work, by someone with the Blender session), or accept the female avatar for both voices for now. Nothing else qualifies.
2. GPL for local TTS. Is it acceptable to ship the optional Piper provider (piper-tts GPL-3.0 and espeak-ng GPL-3.0) as a separate process for internal server use? If the owner or legal says no, drop the local model and keep edge-tts, Google and SAPI5 only; no clean permissive local English engine passed my check.
3. edge-tts terms. It is the existing second source and is unofficial. Confirm the risk is accepted for a company product or budget for Azure AI Speech, which uses the same voice names.
4. TalkingHead pin. Accept pinning to main commit `b3e277b3b46f88e557bf28a2c5612a5b04e075c3` and vendoring the modules into `frontend/avatar/vendor/` (about 300 KB), because the v1.7.0 tag is too old for the CC0 avatar.
5. Asset delivery. 36.8 MB `mpfb.glb` should be downloaded by a script and optionally compressed (Meshopt plus WebP) rather than committed. Choose whether the compressed file is cached in `frontend/avatar/assets/` (git ignored).
6. Sinhala and Tamil lip sync quality gate. Someone who speaks both must watch the HeadAudio plus jaw energy result and approve it, or we show a simpler talking indicator.
7. Optional offline Sinhala: ask the intellisr authors for written permission, or skip. Not blocking.
8. LiveKit worker in the app process: decide to test `AgentServer.run()` in a non main thread, otherwise run the worker as a second process with Whisper loaded twice (plan risk 5).

## Things I could not verify

- Rendering `mpfb.glb` in a browser with TalkingHead main (no browser test run by me). The missing `viseme_sil` shape key is expected to be harmless.
- Blender 5.2 with TalkingHead's MPFB add-on.
- Piper phoneme alignments, Kokoro token timestamps.
- Quality of any Sinhala or Tamil audio (I cannot listen).
- VRoid Studio commercial terms, Microsoft's terms for the edge read aloud endpoint, the exact jsDelivr size limit, and the LiveKit docs page address for dispatch.
- `AgentServer.run` in a thread.
