/* Landing overlay: SltUi.landing.run({language, skip}) -> Promise.
 * Fixed layer above the first screen, removed from the DOM when done. Any
 * pointerdown or keydown skips it at once. See docs/UI_PLAN.md section 3.
 * No libraries; animation is CSS (landing.css), this file builds the DOM,
 * picks the text and the timing, and cleans up. */
(function () {
  "use strict";

  // The logo path is resolved from this script, so it also works from a subfolder page.
  var LOGO = (function () {
    var src = (document.currentScript && document.currentScript.src) || "";
    var i = src.indexOf("/ui/");
    return i >= 0 ? src.slice(0, i) + "/assets/mobitel-logo.png" : "assets/mobitel-logo.png";
  })();

  var FALLBACK = {
    en: ["Welcome to SLT Mobitel", "Your voice assistant in English, Sinhala and Tamil"],
    si: ["SLT Mobitel වෙත ඔබව සාදරයෙන් පිළිගනිමු", "ඔබේ හඬ සහායකයා"],
    ta: ["SLT Mobitel இல் உங்களை வரவேற்கிறோம்", "உங்கள் குரல் உதவியாளர்"],
  };
  var SEEN_KEY = "slt-landing-seen";
  var FULL_MS = 2450; // logo 0.7, ring to 1.1, words to 1.8, subtitle to 2.1, exit 0.35
  var SHORT_MS = 900;
  var REDUCED_MS = 650; // 500 ms still, then one 150 ms fade
  var NS = "http://www.w3.org/2000/svg";
  var running = null;

  function store(kind, key, value) {
    try {
      var s = kind === "session" ? window.sessionStorage : window.localStorage;
      if (value === undefined) return s.getItem(key);
      s.setItem(key, value);
    } catch (err) { /* storage unavailable: behave as a first visit, no preference */ }
    return null;
  }

  function pickLanguage(opt) {
    var cands = [opt, store("local", "slt-language"), navigator.language];
    for (var i = 0; i < cands.length; i++) {
      var l = String(cands[i] || "").toLowerCase().slice(0, 2);
      if (l === "si" || l === "ta" || (l === "en" && i < 2)) return l;
    }
    return "en";
  }

  function texts(lang) {
    var out = FALLBACK[lang].slice();
    try {
      var i18n = window.SltI18n;
      if (i18n && i18n.language() === lang) {
        ["landing.greeting", "landing.subtitle"].forEach(function (key, n) {
          var v = i18n.t(key);
          if (v && v !== key) out[n] = v;
        });
      }
    } catch (err) { /* keep the built-in text */ }
    return out;
  }

  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }

  function svg(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function lowPower() {
    var mem = navigator.deviceMemory;
    return navigator.hardwareConcurrency <= 4 && mem !== undefined && mem <= 2;
  }

  function tone() {
    // Optional intro sound, off unless localStorage slt-sound is "on".
    try {
      if (store("local", "slt-sound") !== "on") return;
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      var t = ctx.currentTime;
      osc.frequency.setValueAtTime(520, t);
      osc.frequency.exponentialRampToValueAtTime(780, t + 0.3);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.06, t + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.45);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.5);
      osc.onended = function () { try { ctx.close(); } catch (e) { /* ignore */ } };
      var p = ctx.resume && ctx.resume();
      if (p && p.catch) p.catch(function () { /* autoplay blocked: silent */ });
    } catch (err) { /* sound is optional */ }
  }

  function build(lang, mode) {
    var t = texts(lang);
    var words = t[0].split(" ").filter(Boolean); // spaces only, never characters
    var root = el("div", "slt-landing");
    root.setAttribute("role", "presentation");
    root.setAttribute("aria-live", "off");
    if (mode === "short") root.classList.add("lnd-short");
    if (mode === "reduced") root.classList.add("lnd-reduce");
    if (lowPower()) root.classList.add("lnd-lite");

    var sr = el("p", "lnd-sr", root);
    sr.textContent = t[0] + ". " + t[1];

    var streams = svg("svg", { "class": "lnd-streams", viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" }, root);
    ["M-5 80 C 25 55, 55 100, 105 60", "M-5 40 C 30 15, 60 60, 105 25", "M-5 62 C 20 90, 70 30, 105 85"].forEach(function (d) {
      svg("path", { d: d, pathLength: "1", "vector-effect": "non-scaling-stroke" }, streams);
    });

    var stage = el("div", "lnd-stage", root);
    stage.setAttribute("aria-hidden", "true");
    var mark = el("div", "lnd-mark", stage);
    el("div", "lnd-glow", mark);
    var ring = svg("svg", { "class": "lnd-ring", viewBox: "0 0 100 100" }, mark);
    var defs = svg("defs", {}, ring);
    var grad = svg("linearGradient", { id: "lnd-ring-grad", x1: "0", y1: "0", x2: "1", y2: "1" }, defs);
    svg("stop", { offset: "0", style: "stop-color:var(--blue,#2f6fed)" }, grad);
    svg("stop", { offset: "1", style: "stop-color:var(--green,#43a047)" }, grad);
    svg("circle", { cx: "50", cy: "50", r: "48", pathLength: "1" }, ring);
    var logo = el("img", "lnd-logo", mark);
    logo.src = LOGO;
    logo.alt = "";
    logo.setAttribute("aria-hidden", "true");
    logo.decoding = "async";

    var title = el("h1", "lnd-title", stage);
    words.forEach(function (w, i) {
      var s = el("span", "lnd-word", title);
      s.textContent = w;
      s.style.setProperty("--i", i);
      if (i < words.length - 1) title.appendChild(document.createTextNode(" "));
    });
    el("div", "lnd-stream", stage);
    var sub = el("p", "lnd-sub", stage);
    sub.textContent = t[1];

    // Words start at 600 ms, 70 ms apart, 280 ms each; the subtitle follows.
    var wordsEnd = 600 + Math.max(0, words.length - 1) * 70 + 280;
    root.style.setProperty("--sub-d", Math.min(wordsEnd, 1800) + "ms");
    return root;
  }

  function run(opts) {
    opts = opts || {};
    if (running) return running;
    if (opts.skip || !document.body) return Promise.resolve();

    var reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    var seen = store("session", SEEN_KEY) === "1";
    var mode = reduced ? "reduced" : seen ? "short" : "full";
    store("session", SEEN_KEY, "1");

    var root;
    try {
      root = build(pickLanguage(opts.language), mode);
    } catch (err) {
      return Promise.resolve(); // never block the app
    }
    document.body.appendChild(root);
    tone();

    var total = mode === "full" ? FULL_MS : mode === "short" ? SHORT_MS : REDUCED_MS;
    var exitMs = mode === "full" ? 350 : mode === "short" ? 250 : 150;
    var timers = [];
    var finish;
    running = new Promise(function (resolve) { finish = resolve; });

    function onVis() { root.classList.toggle("lnd-paused", document.hidden); }
    function done() {
      timers.forEach(clearTimeout);
      window.removeEventListener("pointerdown", done, true);
      window.removeEventListener("keydown", done, true);
      document.removeEventListener("visibilitychange", onVis);
      if (root.parentNode) root.parentNode.removeChild(root);
      running = null;
      finish();
    }

    window.addEventListener("pointerdown", done, true);
    window.addEventListener("keydown", done, true);
    document.addEventListener("visibilitychange", onVis);
    onVis();
    timers.push(setTimeout(function () { root.classList.add("lnd-out"); }, total - exitMs));
    timers.push(setTimeout(done, total));
    return running;
  }

  window.SltUi = window.SltUi || {};
  window.SltUi.landing = { run: run };
})();
