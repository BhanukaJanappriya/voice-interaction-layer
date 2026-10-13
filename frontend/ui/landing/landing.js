/* Landing overlay: SltUi.landing.run({language, skip}) -> Promise.
 * Fixed layer above the first screen, removed from the DOM when done. Any
 * pointerdown or keydown skips it at once. See docs/UI_PLAN.md section 3.
 * No libraries; animation is CSS (landing.css), this file builds the DOM,
 * picks the text and the timing, and cleans up.
 *
 * Sequence on the first visit of a session (about 5.3 s):
 *   0.0 s  an organic gradient shape morphs while the logo scales in
 *   1.6 s  the shape settles into a circle; four glowing rings pulse out of it
 *   1.4 s  the greeting types out with a light caret and a stream under it
 *   3.1 s  the quote types out the same way
 *   4.2 s  the subtitle fades in
 *   4.65 s the logo glides to its place on the first screen (shared element
 *          morph), the rest fades, the overlay is removed at 5.3 s
 * Sinhala and Tamil are never split into characters: the typing is a clip
 * reveal of the whole line, so combining marks stay whole. */
(function () {
  "use strict";

  // The logo path is resolved from this script, so it also works from a subfolder page.
  var LOGO = (function () {
    var src = (document.currentScript && document.currentScript.src) || "";
    var i = src.indexOf("/ui/");
    return i >= 0 ? src.slice(0, i) + "/assets/mobitel-logo.png" : "assets/mobitel-logo.png";
  })();

  // [greeting, subtitle, quote]
  var FALLBACK = {
    en: ["Welcome to SLT Mobitel", "Your voice assistant in English, Sinhala and Tamil", "Sri Lanka's No.1 Connection"],
    si: ["SLT Mobitel වෙත ඔබව සාදරයෙන් පිළිගනිමු", "ඔබේ හඬ සහායකයා", "ශ්‍රී ලංකාවේ අංක 1 සම්බන්ධතාව"],
    ta: ["SLT Mobitel இல் உங்களை வரவேற்கிறோம்", "உங்கள் குரல் உதவியாளர்", "இலங்கையின் நம்பர் 1 இணைப்பு"],
  };
  var KEYS = ["landing.greeting", "landing.subtitle", "landing.quote"];
  var SEEN_KEY = "slt-landing-seen";
  // total, exit (the morph out), per mode
  var TIMING = {
    full: { total: 5300, exit: 650 },
    short: { total: 1700, exit: 450 },
    reduced: { total: 700, exit: 150 },
  };
  var NS = "http://www.w3.org/2000/svg";
  // Blob outlines with the same structure so the browser can interpolate them;
  // the last one is a circle.
  var BLOB = [
    "M100 20 C150 18 182 55 180 100 C178 148 145 182 100 180 C52 178 20 148 20 100 C20 52 52 22 100 20Z",
    "M100 28 C140 10 190 62 172 105 C160 150 130 190 92 176 C50 165 12 140 28 96 C40 55 62 36 100 28Z",
    "M100 14 C160 30 176 70 184 104 C176 150 138 172 96 186 C46 176 24 142 16 98 C30 56 56 24 100 14Z",
    "M100 20 C144.2 20 180 55.8 180 100 C180 144.2 144.2 180 100 180 C55.8 180 20 144.2 20 100 C20 55.8 55.8 20 100 20Z",
  ];
  var RIPPLES = 4;
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
        KEYS.forEach(function (key, n) {
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

  // How many "typing steps" a line has: user perceived characters, so a
  // combined Sinhala or Tamil letter counts once.
  function steps(text) {
    try {
      if (window.Intl && Intl.Segmenter) {
        var n = 0;
        var it = new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)[Symbol.iterator]();
        while (!it.next().done) n++;
        return Math.max(1, n);
      }
    } catch (err) { /* fall through */ }
    return Math.max(1, text.length);
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

  // One typed line: the text is clipped from the right and revealed in steps,
  // a glowing caret rides the edge of the reveal, a light stream grows under it.
  function typedLine(parent, text, cls, delay, dur) {
    var line = el("span", "lnd-line " + cls, parent);
    var type = el("span", "lnd-type", line);
    type.textContent = text;
    el("span", "lnd-caret", line);
    el("span", "lnd-under", line);
    line.style.setProperty("--d", delay + "ms");
    line.style.setProperty("--t", dur + "ms");
    line.style.setProperty("--n", steps(text));
    return line;
  }

  function build(lang, mode) {
    var t = texts(lang);
    var full = mode === "full";
    var root = el("div", "slt-landing");
    root.setAttribute("role", "presentation");
    root.setAttribute("aria-live", "off");
    if (mode === "short") root.classList.add("lnd-short");
    if (mode === "reduced") root.classList.add("lnd-reduce");
    if (lowPower()) root.classList.add("lnd-lite");
    root.style.setProperty("--exit", TIMING[mode].exit + "ms");

    var sr = el("p", "lnd-sr", root);
    sr.textContent = t[0] + ". " + t[2] + ". " + t[1];

    el("div", "lnd-bg", root);

    var streams = svg("svg", { "class": "lnd-streams", viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" }, root);
    ["M-5 80 C 25 55, 55 100, 105 60", "M-5 40 C 30 15, 60 60, 105 25", "M-5 62 C 20 90, 70 30, 105 85"].forEach(function (d) {
      svg("path", { d: d, pathLength: "1", "vector-effect": "non-scaling-stroke" }, streams);
    });

    var stage = el("div", "lnd-stage", root);
    stage.setAttribute("aria-hidden", "true");

    var mark = el("div", "lnd-mark", stage);
    var fx = el("div", "lnd-fx", mark); // everything that fades out while the logo glides away
    el("div", "lnd-glow", fx);
    if (mode !== "reduced") {
      for (var r = 0; r < (full ? RIPPLES : 1); r++) {
        var ripple = el("span", "lnd-ripple", fx);
        ripple.style.setProperty("--rd", (full ? 1500 + r * 650 : 450) + "ms");
      }
    }
    var shape = svg("svg", { "class": "lnd-blob", viewBox: "0 0 200 200" }, fx);
    var defs = svg("defs", {}, shape);
    var grad = svg("linearGradient", { id: "lnd-blob-grad", x1: "0", y1: "0", x2: "1", y2: "1" }, defs);
    svg("stop", { offset: "0", style: "stop-color:var(--blue,#2f6fed)" }, grad);
    svg("stop", { offset: "1", style: "stop-color:var(--green,#43a047)" }, grad);
    var path = svg("path", { d: BLOB[3] }, shape);
    if (full) {
      // The morph: organic shapes that settle into the circle at 1.6 s.
      var values = [BLOB[3], BLOB[0], BLOB[1], BLOB[2], BLOB[0], BLOB[3]].join(";");
      svg("animate", {
        attributeName: "d", values: values, keyTimes: "0;0.2;0.4;0.6;0.8;1",
        dur: "1600ms", begin: "0s", fill: "freeze", calcMode: "spline",
        keySplines: "0.4 0 0.2 1;0.4 0 0.2 1;0.4 0 0.2 1;0.4 0 0.2 1;0.4 0 0.2 1",
      }, path);
    }
    var logo = el("img", "lnd-logo", mark);
    logo.src = LOGO;
    logo.alt = "";
    logo.setAttribute("aria-hidden", "true");
    logo.decoding = "async";

    var copy = el("div", "lnd-copy", stage);
    var title = el("h1", "lnd-title", copy);
    if (mode === "reduced") {
      title.textContent = t[0];
    } else {
      typedLine(title, t[0], "lnd-greet", full ? 1400 : 350, full ? 1500 : 600);
    }
    if (full) {
      var quote = el("p", "lnd-quote", copy);
      typedLine(quote, "“" + t[2] + "”", "lnd-q", 3100, 1100);
    }
    if (mode !== "short") {
      var sub = el("p", "lnd-sub", copy);
      sub.textContent = t[1];
      root.style.setProperty("--sub-d", (full ? 4200 : 0) + "ms");
    }
    return root;
  }

  // Shrinks a typed line that is wider than the screen so it stays on one line,
  // and tells the caret how far to travel.
  function fit(root) {
    var max = Math.min(window.innerWidth - 40, 520);
    var lines = root.querySelectorAll(".lnd-line");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var type = line.querySelector(".lnd-type");
      var w = type.scrollWidth;
      if (w > max) {
        var size = parseFloat(getComputedStyle(type).fontSize);
        type.style.fontSize = (size * max / w * 0.97) + "px";
        w = type.scrollWidth;
      }
      line.style.setProperty("--w", w + "px");
    }
  }

  // The logo glides from its place in the landing to the logo of the screen
  // underneath (a shared element morph), using transform only.
  function glide(root) {
    var logo = root.querySelector(".lnd-logo");
    var target = document.querySelector(".screen.active .brand-logo") || document.querySelector(".brand-logo");
    if (!logo || !target || !logo.animate) return null;
    var a = logo.getBoundingClientRect();
    var b = target.getBoundingClientRect();
    if (!a.width || !b.width || b.width < 20) return null;
    var dx = b.left + b.width / 2 - (a.left + a.width / 2);
    var dy = b.top + b.height / 2 - (a.top + a.height / 2);
    var s = b.width / a.width;
    target.style.opacity = "0"; // the landing's logo takes its place until the overlay is gone
    try {
      logo.animate(
        [{ transform: "translate(0px, 0px) scale(1)" }, { transform: "translate(" + dx + "px, " + dy + "px) scale(" + s + ")" }],
        { duration: parseFloat(getComputedStyle(root).getPropertyValue("--exit")) || 600, easing: "cubic-bezier(0.65, 0, 0.35, 1)", fill: "forwards" }
      );
    } catch (err) {
      target.style.removeProperty("opacity");
      return null;
    }
    return target;
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
    try { fit(root); } catch (err) { /* the line just stays at its natural size */ }
    tone();

    var total = TIMING[mode].total;
    var exitMs = TIMING[mode].exit;
    var timers = [];
    var finish;
    var gliding = null;
    running = new Promise(function (resolve) { finish = resolve; });

    function onVis() { root.classList.toggle("lnd-paused", document.hidden); }
    function done() {
      timers.forEach(clearTimeout);
      window.removeEventListener("pointerdown", done, true);
      window.removeEventListener("keydown", done, true);
      document.removeEventListener("visibilitychange", onVis);
      if (gliding) gliding.style.removeProperty("opacity");
      if (root.parentNode) root.parentNode.removeChild(root);
      running = null;
      finish();
    }

    window.addEventListener("pointerdown", done, true);
    window.addEventListener("keydown", done, true);
    document.addEventListener("visibilitychange", onVis);
    onVis();
    timers.push(setTimeout(function () {
      root.classList.add("lnd-out");
      if (mode !== "reduced") gliding = glide(root);
    }, total - exitMs));
    timers.push(setTimeout(done, total));
    return running;
  }

  window.SltUi = window.SltUi || {};
  window.SltUi.landing = { run: run };
})();
