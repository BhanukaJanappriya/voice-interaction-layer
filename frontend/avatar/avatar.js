/*
 * avatar.js: the TalkingHead based 3D avatar renderer (ES module).
 *
 * Exposes window.avatarRenderer. It is loaded by loader.js through a dynamic
 * import() so a browser without import maps never sees a module error, and the
 * legacy bot3d.js / flat face stays in charge whenever this cannot run (it
 * then dispatches "avatar:fallback" on document, detail {reason}).
 *
 * Where the mouth comes from, in order of honesty:
 *   1. Audio that really reaches the browser (attachAudioStream for LiveKit,
 *      speakAudio for voicekit): HeadAudio turns the audio into visemes, which
 *      is language independent, plus an energy driven jaw floor so Sinhala and
 *      Tamil, which the English trained model reads badly, still move. This
 *      path only ANALYSES the audio. The page's own <audio> element or the
 *      VoiceKit instance plays it, so nothing is heard twice.
 *   2. Provider word timings (voicekit, English only): TalkingHead's own text
 *      to viseme module places visemes at the word times on the audio clock.
 *   3. speakSentence(text) is TEXT BASED mouth animation. It is NOT audio lip
 *      sync: it exists for the WebSocket transport, where the server plays the
 *      sound and the browser has no audio to analyse.
 *
 * Own code is kept small on purpose; the heavy lifting is in vendor/.
 */

const BASE = new URL("./", import.meta.url);
const ASSET = (name) => new URL("assets/" + name, BASE).href;
const VENDOR = (name) => new URL("vendor/" + name, BASE).href;

const INIT_TIMEOUT_MS = 20000;
const STATES = ["idle", "calibrating", "listening", "thinking", "speaking", "error"];
const VISEMES = ["aa", "E", "I", "O", "U", "PP", "SS", "TH", "DD", "FF", "kk", "nn", "RR", "CH"].map((v) => "viseme_" + v);

// Dynamic bones copied from TalkingHead's siteconfig.js entry for MPFB: they
// give the female model's hair and chest a little secondary motion.
const FEMALE_BONES = [
  { bone: "LeftBreast", type: "point", stiffness: 600, damping: 20, deltaLocal: [0, 0.01, 0], deltaWorld: [0, -0.02, 0], limits: [[-0.02, 0.02], [-0.02, 0.06], [-0.02, 0.02], [-0.5, 0.5]] },
  { bone: "RightBreast", type: "point", stiffness: 600, damping: 20, deltaLocal: [0, 0.01, 0], deltaWorld: [0, -0.02, 0], limits: [[-0.02, 0.02], [-0.02, 0.06], [-0.02, 0.02], [-0.5, 0.5]] },
  { bone: "Ponytail1", type: "mix2", stiffness: 100, damping: 4, limits: [null, null, [null, 0.02], null], pivot: true },
  { bone: "Ponytail2", type: "mix2", stiffness: 150, damping: 4, limits: [null, null, [null, 0.01], null] },
  { bone: "Ponytail3", type: "mix2", stiffness: 200, damping: 4, limits: [null, null, [null, 0.01], null] },
];
const BASELINE = { headRotateX: -0.01, eyeBlinkLeft: 0.05, eyeBlinkRight: 0.05 };
const AVATARS = {
  female: { file: "mpfb.glb", body: "F", modelDynamicBones: FEMALE_BONES },
  // The male file comes from another build step. Its bone names are unknown
  // here, so no dynamic bones are configured for it.
  male: { file: "male.glb", body: "M" },
};

// What each pipeline state looks like. mt values are eased in with setValue
// and cleared on the next state change, so states never stack up.
const LOOKS = {
  idle: { mood: "neutral", mt: {} },
  calibrating: { mood: "neutral", mt: { browInnerUp: 0.15 } },
  listening: { mood: "neutral", mt: { headRotateZ: 0.07, browInnerUp: 0.2 } },
  thinking: { mood: "neutral", mt: { browInnerUp: 0.45, headRotateZ: -0.08, mouthPressLeft: 0.25, eyeSquintLeft: 0.2 } },
  speaking: { mood: "happy", mt: {} },
  error: { mood: "sad", mt: { browInnerUp: 0.5 } },
};

const S = {
  head: null, container: null, stage: null, opts: {},
  active: false, destroyed: false, state: "idle", gender: "female", shownGender: null,
  audio: null, tracks: { timing: null, text: null },
  jaw: 0, jawEnergy: 0, mouth: 0, timers: [], loadChain: Promise.resolve(),
  frames: 0, fpsStamp: 0, fpsValue: 0, io: null, paused: false,
  sync: { audioOnset: null, quiet: 0, mouthQuiet: 0, mouthOpen: false, offsets: [], audioOn: false, timer: 0 },
  headaudioByCtx: new WeakMap(), appliedMt: [], gestureTimer: 0, nodTimer: 0, gazeAt: 0, onPointer: null,
};
let readyResolve = () => {};
let readyPromise = new Promise((r) => (readyResolve = r));

function fallback(reason, detail) {
  S.active = false;
  console.warn("avatar: falling back to the legacy renderer:", reason, detail || "");
  document.dispatchEvent(new CustomEvent("avatar:fallback", { bubbles: true, detail: { reason, message: String(detail || "") } }));
  readyResolve(false);
}

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

async function assetExists(name) {
  try {
    const r = await fetch(ASSET(name), { method: "HEAD", cache: "no-store" });
    // A static mount that answers 200 with an HTML page is not a GLB.
    return r.ok && !(r.headers.get("content-type") || "").includes("text/html");
  } catch (e) {
    return false;
  }
}

/** Writes an animation value for one morph target, tolerating missing ones. */
function mt(key, value) {
  const o = S.head && S.head.mtAvatar && S.head.mtAvatar[key];
  if (o) Object.assign(o, { newvalue: value, needsUpdate: true });
}

// ---------------------------------------------------------------- visemes

/** Triangle envelope events: {t0, t1, t2, key, level}, times in ms on a track clock. */
function envelope(track, now, out) {
  const ev = track.events;
  let keep = 0;
  for (let i = 0; i < ev.length; i++) {
    const e = ev[i];
    if (e.t2 < now - 500) continue; // long finished
    ev[keep++] = e;
    if (now < e.t0 || now > e.t2) continue;
    const v = now < e.t1 ? (e.level * (now - e.t0)) / Math.max(1, e.t1 - e.t0) : (e.level * (e.t2 - now)) / Math.max(1, e.t2 - e.t1);
    if (v > (out[e.key] || 0)) out[e.key] = v;
  }
  ev.length = keep;
}

/** Visemes for one word from TalkingHead's own English module, shaped like its speakAudio path. */
function wordEvents(word, startMs, durMs) {
  const head = S.head;
  const proc = head.lipsync && head.lipsync.en;
  if (!proc || !word) return [];
  const val = head.lipsyncWordsToVisemes(head.lipsyncPreProcessText(word, "en"), "en");
  if (!val || !val.visemes || !val.visemes.length) return [];
  const n = val.visemes.length;
  const total = val.times[n - 1] + val.durations[n - 1];
  const dur = Math.min(durMs, n * 200);
  const level = 0.6 + 0.4 * Math.min(1, Math.max(0, (durMs - n * 150) / Math.max(1, durMs)));
  if (total <= 0) return [];
  return val.visemes.map((v, j) => {
    const t = startMs + (val.times[j] / total) * dur;
    const d = (val.durations[j] / total) * dur;
    return {
      t0: t - Math.min(60, (2 * d) / 3), t1: t + Math.min(25, d / 2), t2: t + d + Math.min(60, d / 2),
      key: "viseme_" + v, level: v === "PP" || v === "FF" ? 0.9 : level,
    };
  });
}

/** Rough vowel and lip consonant mapping, used only for text based animation. */
function textEvents(text, startMs, perChar) {
  const ev = [];
  let t = startMs;
  const map = { a: "aa", e: "E", i: "I", o: "O", u: "U", b: "PP", m: "PP", p: "PP", f: "FF", v: "FF" };
  const cycle = ["aa", "O", "E", "U", "I"]; // scripts we cannot read (Sinhala, Tamil): keep a speech like rhythm
  for (const word of text.trim().split(/\s+/)) {
    for (const ch of word.toLowerCase()) {
      const latin = /[a-z]/.test(ch);
      let v = latin ? map[ch] : /\p{L}/u.test(ch) ? cycle[ch.codePointAt(0) % cycle.length] : null;
      if (!v) { t += perChar * 0.4; continue; }
      const d = perChar * (latin ? 1.1 : 0.9);
      ev.push({ t0: t - 25, t1: t + d * 0.35, t2: t + d + 30, key: "viseme_" + v, level: v === "PP" || v === "FF" ? 0.85 : 0.65 });
      t += d;
    }
    t += perChar * 1.2; // gap between words
    if (/[.,!?;]$/.test(word)) t += perChar * 3;
  }
  return { events: ev, end: t };
}

// ------------------------------------------------------------------ audio

function rms(analyser, buf) {
  analyser.getFloatTimeDomainData(buf);
  let s = 0;
  for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
  return Math.sqrt(s / buf.length);
}

async function headAudioFor(ctx) {
  if (S.headaudioByCtx.has(ctx)) return S.headaudioByCtx.get(ctx);
  const p = (async () => {
    if (!ctx.audioWorklet) throw new Error("no AudioWorklet (needs https or localhost)");
    const { HeadAudio } = await import(VENDOR("headaudio.min.mjs"));
    await ctx.audioWorklet.addModule(VENDOR("headworklet.min.mjs"));
    // silMode 0: no manual silence calibration, rely on the model's silence prototypes.
    const node = new HeadAudio(ctx, { processorOptions: {}, parameterData: { vadMode: 1, vadGateActiveDb: -40, vadGateInactiveDb: -60, silMode: 0 } });
    await node.loadModel(VENDOR("model-en-mixed.bin"));
    node.onvalue = (key, v) => mt(key, v);
    return node;
  })();
  S.headaudioByCtx.set(ctx, p);
  p.catch(() => S.headaudioByCtx.delete(ctx));
  return p;
}

function detachAudio() {
  const a = S.audio;
  S.audio = null;
  if (!a) return;
  try { a.node && a.node.disconnect(); } catch (e) { /* already gone */ }
  try { a.analyserOwn && a.analyser.disconnect(); } catch (e) { /* already gone */ }
  if (a.headaudio) {
    try { a.tap.disconnect(a.headaudio); } catch (e) { /* not connected */ }
    a.headaudio.resetAll();
  }
}

/**
 * Wires a source node to an analyser (own or given) and to HeadAudio.
 * `node` is where audio is tapped from; nothing is ever connected to the
 * destination here.
 */
async function attach(kind, ctx, tap, analyser, extra) {
  detachAudio();
  const audio = Object.assign({ kind, ctx, tap, analyser, buf: new Float32Array(analyser.fftSize), headaudio: null }, extra);
  S.audio = audio;
  startSyncTimer();
  try {
    const ha = await headAudioFor(ctx);
    if (S.audio === audio) {
      tap.connect(ha);
      audio.headaudio = ha;
    }
  } catch (err) {
    // The jaw energy floor below still works without HeadAudio.
    console.warn("avatar: HeadAudio unavailable, using the energy driven jaw only:", err.message || err);
  }
  return audio;
}

async function attachAudioStream(stream) {
  if (!S.active || !stream) return false;
  const ctx = S.head.audioCtx;
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(analyser);
  await attach("stream", ctx, src, analyser, { node: src, analyserOwn: true });
  return true;
}

/**
 * source: a VoiceKit instance, an AnalyserNode, a MediaStream, or an media
 * element. timings: [{text, start_ms, duration_ms}] from the utterance start.
 * opts.language ("en" etc.) decides whether word timings drive visemes; the
 * text to viseme module is English only, other languages use audio analysis.
 * Returns {addTimings(words), stop()}; voicekit sends timings in batches.
 */
async function speakAudio(source, timings, opts) {
  opts = opts || {};
  const handle = { addTimings() {}, stop: () => api.stop() };
  if (!S.active || !source) return handle;
  if (typeof MediaStream !== "undefined" && source instanceof MediaStream) {
    await attachAudioStream(source);
    return handle;
  }
  if (typeof HTMLMediaElement !== "undefined" && source instanceof HTMLMediaElement) {
    // captureStream taps what the element plays without rerouting its output.
    const cap = source.captureStream || source.mozCaptureStream;
    if (!cap) { console.warn("avatar: this browser cannot capture a media element"); return handle; }
    await attachAudioStream(cap.call(source));
    return handle;
  }
  const isKit = source.analyser && source.audioContext;
  const analyser = isKit ? source.analyser : source;
  const ctx = isKit ? source.audioContext : analyser.context;
  if (!analyser || !ctx) return handle;
  const t0 = ctx.currentTime;
  // voicekit tells us where its utterance clock starts; anything else starts now.
  const clockStart = () => (isKit && source.audioStartTime ? source.audioStartTime : t0);
  const latency = () => (ctx.outputLatency || 0) * 1000;
  await attach(isKit ? "voicekit" : "analyser", ctx, analyser, analyser, { node: null, analyserOwn: false });
  const english = !opts.language || String(opts.language).toLowerCase().startsWith("en");
  const track = { events: [], now: () => (ctx.currentTime - clockStart()) * 1000 - latency() };
  S.tracks.timing = track;
  handle.addTimings = (words) => {
    if (!english) return;
    for (const w of words || []) track.events.push(...wordEvents(w.text, w.start_ms, w.duration_ms));
  };
  handle.addTimings(timings);
  return handle;
}

/** TEXT BASED mouth animation, not audio lip sync (see the file header). */
function speakSentence(text, opts) {
  if (!S.active || !text) return;
  const perChar = (opts && opts.msPerChar) || 62; // about 15 characters per second
  let track = S.tracks.text;
  const now = performance.now();
  if (!track || track.end < now) track = S.tracks.text = { events: [], end: now, now: () => performance.now() };
  const { events, end } = textEvents(String(text), track.end, perChar);
  track.events.push(...events);
  track.end = end + 150;
}

// ------------------------------------------------------------ frame driver

function frame(dt) {
  const now = performance.now();
  S.frames++;
  if (now - S.fpsStamp >= 1000) {
    S.fpsValue = (S.frames * 1000) / (now - S.fpsStamp);
    S.frames = 0;
    S.fpsStamp = now;
  }
  const a = S.audio;
  let level = 0;
  if (a) {
    level = rms(a.analyser, a.buf);
    // Word timings win over audio analysis while they have events to play.
    const timed = S.tracks.timing && S.tracks.timing.events.length;
    if (a.headaudio && !timed) a.headaudio.update(dt);
  }
  const out = {};
  for (const name of ["timing", "text"]) {
    const t = S.tracks[name];
    if (!t) continue;
    envelope(t, t.now(), out);
    if (name === "text" && !t.events.length && t.end < now) S.tracks.text = null;
  }
  let vis = 0;
  for (const k of VISEMES) {
    if (out[k]) { mt(k, out[k]); if (out[k] > vis) vis = out[k]; }
    else if (S.prevVis && S.prevVis[k]) mt(k, 0);
  }
  S.prevVis = out;

  // Energy floor: loud audio opens the jaw even when the visemes say nothing,
  // which is what keeps Sinhala and Tamil lively. Visemes already open the
  // mouth, so their share is subtracted to avoid an over wide mouth.
  const target = Math.max(0, Math.min(1, (level - 0.012) / 0.12)) * 0.4 - vis * 0.3;
  const k = 1 - Math.exp(-dt / (target > S.jaw ? 35 : 90));
  S.jaw += (Math.max(0, target) - S.jaw) * k;
  if (S.jaw > 0.004 || S.jawWritten) { mt("jawOpen", S.jaw < 0.004 ? 0 : S.jaw); S.jawWritten = S.jaw >= 0.004; }

  trackMouth(now);
}

/** Mouth openness as it was last applied, for the sync measurement. */
function mouthLevel() {
  const m = S.head.mtAvatar;
  let v = (m.jawOpen && m.jawOpen.applied) || 0;
  for (const k of VISEMES) if (m[k] && m[k].applied > v) v = m[k].applied;
  return v;
}

// ------------------------------------------------------ sync measurement

function startSyncTimer() {
  if (S.sync.timer) return;
  // A 10 ms poll gives the audio side finer resolution than the 30 fps render loop.
  S.sync.timer = setInterval(() => {
    const a = S.audio;
    if (!a) return;
    const level = rms(a.analyser, a.buf);
    const sy = S.sync, t = performance.now();
    if (level > 0.02 && !sy.audioOn && t - sy.quiet > 200) { sy.audioOnset = t; sy.audioOn = true; sy.latency = (a.ctx.outputLatency || 0) * 1000; }
    if (level < 0.01) { if (sy.audioOn) sy.quiet = t; sy.audioOn = false; }
  }, 10);
}

function trackMouth(now) {
  const sy = S.sync, v = mouthLevel();
  if (v < 0.06) { if (sy.mouthOpen) sy.mouthQuiet = now; sy.mouthOpen = false; }
  else if (v > 0.12 && !sy.mouthOpen) {
    sy.mouthOpen = true;
    if (sy.audioOnset !== null && now - sy.audioOnset < 700 && now >= sy.audioOnset - 150) {
      const raw = now - sy.audioOnset;
      sy.offsets.push({ rawMs: Math.round(raw), heardMs: Math.round(raw - (sy.latency || 0)), outputLatencyMs: Math.round(sy.latency || 0) });
      if (sy.offsets.length > 50) sy.offsets.shift();
    }
    sy.audioOnset = null;
  }
}

function median(xs) {
  if (!xs.length) return null;
  const s = xs.slice().sort((p, q) => p - q), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/**
 * Estimates audio to mouth delay. Audio onset is the first time the analyser
 * RMS rises above 0.02 after 200 ms of quiet; mouth onset is the first frame
 * where max(jawOpen, viseme) passes 0.12. Positive means the mouth moves later
 * than the audio. rawMs is relative to when the audio entered the analyser;
 * heardMs also subtracts ctx.outputLatency, an estimate of when it is actually
 * audible. The mouth side is sampled once per rendered frame (about 33 ms), so
 * read single numbers as +/- one frame and trust the median.
 */
function measureSyncOffset() {
  const o = S.sync.offsets;
  const result = {
    count: o.length, offsets: o.slice(),
    medianRawMs: median(o.map((x) => x.rawMs)), medianHeardMs: median(o.map((x) => x.heardMs)),
    print() { console.log(`avatar sync: n=${this.count} median raw ${this.medianRawMs} ms, median heard ${this.medianHeardMs} ms (positive = mouth late)`); return this; },
  };
  return result;
}

// ------------------------------------------------------ state and gesture

function clearTimers() {
  clearTimeout(S.gestureTimer);
  clearTimeout(S.nodTimer);
}

function pick(list) {
  return list[Math.floor(Math.random() * list.length)];
}

function gestureLoop(first) {
  clearTimeout(S.gestureTimer);
  if (S.state !== "speaking" || !S.head) return;
  S.gestureTimer = setTimeout(() => {
    if (S.state !== "speaking" || !S.active) return;
    // Hands are sparse on purpose: a gesture every several seconds, thumbs up rarely.
    const g = Math.random() < 0.12 ? "thumbup" : pick(["handup", "handup", "index", "ok", "side"]);
    try { S.head.playGesture(g, 2, Math.random() < 0.5); } catch (e) { /* gesture missing */ }
    if (Math.random() < 0.5) nod();
    gestureLoop(false);
  }, first ? 2500 : 6000 + Math.random() * 5000);
}

function nod() {
  const h = S.head;
  if (!h || !h.animFactory) return;
  h.animQueue.push(h.animFactory({ name: "nod", dt: [250, 300, 250, 350], vs: { headMove: [0], headRotateX: [0.14, 0.02, 0.1, 0] } }));
}

function listeningLoop() {
  clearTimeout(S.nodTimer);
  if (S.state !== "listening" || !S.active) return;
  S.nodTimer = setTimeout(() => {
    if (S.state !== "listening") return;
    nod(); // a small acknowledging nod while the user talks
    S.head.lookAtCamera(1500);
    listeningLoop();
  }, 4500 + Math.random() * 3500);
}

function applyState(state) {
  const h = S.head;
  if (!h || !S.active) return;
  const look = LOOKS[state];
  for (const key of S.appliedMt) h.setValue(key, null);
  S.appliedMt = Object.keys(look.mt);
  for (const [key, v] of Object.entries(look.mt)) h.setValue(key, v, 500);
  try { h.setMood(look.mood); } catch (e) { /* unknown mood */ }
  clearTimers();
  try { h.stopGesture(600); } catch (e) { /* none playing */ }
  if (state === "thinking") {
    const b = S.container.getBoundingClientRect();
    h.lookAt(b.left + b.width * 1.1, b.top - b.height * 0.15, 4000); // up and aside
  } else if (state === "listening") {
    h.makeEyeContact(3000);
    h.lookAtCamera(1500);
    listeningLoop();
  } else if (state === "speaking") {
    h.lookAtCamera(1200);
    gestureLoop(true);
  } else if (state === "error") {
    h.lookAhead(2500);
  } else {
    h.lookAtCamera(1500);
  }
}

function setState(state) {
  state = STATES.includes(state) ? state : "idle";
  const leaving = S.state;
  S.state = state;
  // The text based mouth only makes sense while speaking; real audio is left alone.
  if (leaving === "speaking" && state !== "speaking") S.tracks.text = null;
  if (S.active) applyState(state);
}

/** Barge in: silence the mouth and every queued animation at once. */
function stop() {
  S.tracks.text = null;
  S.tracks.timing = null;
  const a = S.audio;
  if (a && a.headaudio) a.headaudio.resetAll();
  S.jaw = 0;
  S.prevVis = null;
  if (S.head && S.head.mtAvatar) {
    for (const k of VISEMES) mt(k, 0);
    mt("jawOpen", 0);
    try { S.head.stopSpeaking(); } catch (e) { /* nothing queued */ }
    try { S.head.stopGesture(300); } catch (e) { /* none */ }
  }
  clearTimeout(S.gestureTimer);
}

// ------------------------------------------------------ loading and gender

function setupGaze() {
  let last = 0;
  S.onPointer = (e) => {
    const now = performance.now();
    if (!S.active || now - last < 700 || !["idle", "listening", "calibrating"].includes(S.state)) return;
    last = now;
    S.head.lookAt(e.clientX, e.clientY, 900);
  };
  window.addEventListener("pointermove", S.onPointer, { passive: true });
}

async function loadAvatar(gender) {
  const spec = AVATARS[gender];
  await S.head.showAvatar({
    url: ASSET(spec.file), body: spec.body, avatarMood: "neutral", lipsyncLang: "en",
    baseline: BASELINE, modelDynamicBones: spec.modelDynamicBones,
  });
  S.shownGender = gender;
}

async function setGender(gender) {
  gender = gender === "male" ? "male" : "female";
  S.gender = gender;
  const run = async () => {
    if (!S.active) return { gender: S.shownGender, fallback: false };
    let want = gender;
    let fell = false;
    if (want === "male" && !(await assetExists(AVATARS.male.file))) {
      want = "female";
      fell = true;
      document.dispatchEvent(new CustomEvent("avatar:gender-fallback", {
        bubbles: true,
        detail: { requested: "male", shown: S.shownGender || "female", reason: "male-asset-missing", message: "male.glb is not installed, showing the female avatar" },
      }));
    }
    if (want !== S.shownGender && S.gender === gender) {
      try {
        await loadAvatar(want);
        applyState(S.state);
      } catch (err) {
        console.warn("avatar: gender swap failed, keeping the current avatar:", err);
        return { gender: S.shownGender, fallback: true, error: String(err) };
      }
    }
    return { gender: S.shownGender, fallback: fell };
  };
  S.loadChain = S.loadChain.then(run, run);
  return S.loadChain;
}

async function init(container, opts) {
  if (S.head) destroy();
  S.destroyed = false;
  readyPromise = new Promise((r) => (readyResolve = r));
  opts = S.opts = opts || {};
  S.container = container;
  if (!container) { fallback("no-container"); return readyPromise; }
  if (typeof HTMLScriptElement !== "undefined" && HTMLScriptElement.supports && !HTMLScriptElement.supports("importmap")) {
    fallback("no-importmap");
    return readyPromise;
  }
  if (!hasWebGL()) { fallback("no-webgl"); return readyPromise; }

  const timeoutMs = opts.initTimeoutMs || INIT_TIMEOUT_MS;
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; fallback("init-timeout", timeoutMs + " ms"); destroy(); }, timeoutMs);
  try {
    const { TalkingHead } = await import(VENDOR("talkinghead.mjs"));
    if (timedOut) return readyPromise;
    S.gender = opts.gender === "male" ? "male" : "female";
    const first = S.gender === "male" && (await assetExists(AVATARS.male.file)) ? "male" : "female";
    if (!(await assetExists(AVATARS[first].file))) throw Object.assign(new Error(AVATARS[first].file + " is missing, run fetch_assets.py"), { reason: "asset-missing" });
    const stage = document.createElement("div");
    stage.style.cssText = "position:absolute;inset:0;width:100%;height:100%";
    container.appendChild(stage);
    S.stage = stage;
    S.head = new TalkingHead(stage, {
      lipsyncModules: ["en"], cameraView: opts.cameraView || "upper", cameraDistance: opts.cameraDistance === undefined ? -0.45 : opts.cameraDistance, modelFPS: opts.fps || 30,
      modelPixelRatio: Math.min(window.devicePixelRatio || 1, 2), avatarMood: "neutral",
      cameraRotateEnable: false, cameraPanEnable: false, cameraZoomEnable: false,
      mixerGainSpeech: 1, update: frame,
    });
    await loadAvatar(first);
    if (timedOut || S.destroyed) return readyPromise;
    S.active = true;
    S.fpsStamp = performance.now();
    setupGaze();
    watchVisibility();
    applyState(S.state);
    if (S.gender === "male" && first === "female") {
      document.dispatchEvent(new CustomEvent("avatar:gender-fallback", { bubbles: true, detail: { requested: "male", shown: "female", reason: "male-asset-missing", message: "male.glb is not installed, showing the female avatar" } }));
    }
    readyResolve(true);
  } catch (err) {
    if (!timedOut) {
      const reason = err.reason || (/Failed to (fetch|resolve)|import/i.test(String(err && err.message)) ? "import-failed" : "init-failed");
      fallback(reason, err && err.message);
      destroy();
    }
  } finally {
    clearTimeout(timer);
  }
  return readyPromise;
}

/** Pauses rendering while the avatar is on a hidden screen, to save GPU. */
function watchVisibility() {
  if (!window.IntersectionObserver) return;
  S.io = new IntersectionObserver((entries) => {
    const h = S.head;
    if (!h || !S.active) return;
    const visible = entries[entries.length - 1].isIntersecting;
    // head.stop() would also suspend the audio context the analysis uses, so the loop flag is flipped directly.
    if (!visible && h.isRunning) { h.isRunning = false; S.paused = true; }
    else if (visible && S.paused) { S.paused = false; h.start(); }
  });
  S.io.observe(S.container);
}

function destroy() {
  S.destroyed = true;
  S.active = false;
  clearTimers();
  clearInterval(S.sync.timer);
  S.sync.timer = 0;
  detachAudio();
  if (S.io) S.io.disconnect();
  S.io = null;
  if (S.onPointer) window.removeEventListener("pointermove", S.onPointer);
  S.onPointer = null;
  if (S.head) { try { S.head.dispose(); } catch (e) { /* partly built */ } }
  if (S.stage && S.stage.parentNode) S.stage.parentNode.removeChild(S.stage);
  S.head = null;
  S.stage = null;
  S.tracks = { timing: null, text: null };
  S.shownGender = null;
  S.paused = false;
}

const api = {
  init, setGender, setState, speakAudio, attachAudioStream, speakSentence, stop, destroy,
  measureSyncOffset, isActive: () => S.active && !S.destroyed,
  get ready() { return readyPromise; },
  get gender() { return S.shownGender; },
  fps: () => Math.round(S.fpsValue * 10) / 10,
  _debug: S,
};
window.avatarRenderer = api;
export default api;
