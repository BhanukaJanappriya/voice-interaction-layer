/*
 * loader.js: decides at page load whether the TalkingHead avatar runs.
 *
 * A plain script (no modules, no syntax newer than ES5 outside a guarded
 * dynamic import) so it can be included in any browser and can never break
 * the page. It reads avatar_renderer from GET /api/livekit/config. Only the
 * value "talkinghead" starts avatar.js; a 404, a network error, anything else
 * or a browser without import maps leaves bot3d.js and the flat face exactly
 * as they were. While TalkingHead loads (a few seconds for the 37 MB model)
 * the legacy face stays visible, and "has-th" is added to #bot-avatar only once
 * the renderer is really running. "avatar:fallback" removes it again.
 *
 * It also defines window.avatarBridge, a safe facade for app.js: every method
 * can be called at any time (before load, after fallback, on legacy) and does
 * nothing harmful. State, gender and the audio stream are remembered and
 * replayed when the renderer becomes ready.
 *
 * Developer override: add ?avatar=talkinghead or ?avatar=legacy to the page URL.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var base = script && script.src ? script.src.replace(/[^\/]*$/, "") : "/avatar/";
  var remembered = { state: "idle", gender: null, stream: null, variant: "original" };
  var mode = "legacy"; // legacy | loading | talkinghead | fallback

  function renderer() {
    var r = window.avatarRenderer;
    return r && r.isActive && r.isActive() ? r : null;
  }

  function guard(fn) {
    return function () {
      try {
        var r = renderer();
        if (r) return fn(r, arguments);
      } catch (err) {
        if (window.console) console.warn("avatarBridge:", err);
      }
      return undefined;
    };
  }

  window.avatarBridge = {
    get mode() { return mode; },
    isActive: function () { return !!renderer(); },
    setState: function (s) { remembered.state = s; guard(function (r) { r.setState(s); })(); },
    setGender: function (g) { remembered.gender = g; return guard(function (r) { return r.setGender(g); })(); },
    attachAudioStream: function (stream) { remembered.stream = stream; return guard(function (r, a) { return r.attachAudioStream(a[0]); }).call(null, stream); },
    speakAudio: guard(function (r, a) { return r.speakAudio(a[0], a[1], a[2]); }),
    speakSentence: guard(function (r, a) { return r.speakSentence(a[0], a[1]); }),
    stop: guard(function (r) { r.stop(); }),
  };

  function findContainer() {
    return document.getElementById("bot-avatar");
  }

  function addStyles() {
    try {
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = base + "avatar.css";
      document.head.appendChild(link);
    } catch (err) { /* cosmetic only */ }
  }

  function ensureImportMap() {
    if (document.querySelector('script[type="importmap"]')) return true;
    if (!(window.HTMLScriptElement && HTMLScriptElement.supports && HTMLScriptElement.supports("importmap"))) return false;
    var map = document.createElement("script");
    map.type = "importmap";
    map.textContent = JSON.stringify({
      imports: {
        three: base + "vendor/three/build/three.module.js",
        "three/addons/": base + "vendor/three/examples/jsm/",
      },
    });
    // Works only while no module script has been loaded yet, which holds on
    // this page (bot3d.js and app.js are classic scripts).
    document.head.appendChild(map);
    return true;
  }

  function teardown(stage) {
    var c = findContainer();
    if (c) c.classList.remove("has-th");
    if (stage && stage.parentNode) stage.parentNode.removeChild(stage);
    mode = "fallback";
  }

  function start() {
    var container = findContainer();
    if (!container) return;
    var stage = document.createElement("div");
    stage.id = "th-avatar";
    stage.className = "th-stage";
    container.appendChild(stage);
    addStyles();
    mode = "loading";
    document.addEventListener("avatar:fallback", function () { teardown(stage); });
    if (!ensureImportMap()) { teardown(stage); return; }
    // Wrapped in Function so a browser without dynamic import() never fails to parse this file.
    new Function("u", "return import(u)")(base + "avatar.js")
      .then(function () {
        var r = window.avatarRenderer;
        return r.init(stage, { gender: remembered.gender === "male" ? "male" : "female", variant: remembered.variant });
      })
      .then(function (ok) {
        if (!ok) return; // the fallback event already cleaned up
        container.classList.add("has-th");
        mode = "talkinghead";
        var r = window.avatarRenderer;
        r.setState(remembered.state);
        if (remembered.stream) r.attachAudioStream(remembered.stream);
      })
      .catch(function (err) {
        if (window.console) console.warn("avatar loader: import failed, staying on the legacy avatar:", err);
        teardown(stage);
      });
  }

  function decide(value, variant) {
    if (variant) remembered.variant = String(variant).toLowerCase();
    if (value === "talkinghead") start();
  }

  function begin() {
    try {
      var m = /[?&]avatar=(\w+)/.exec(window.location.search);
      if (m) {
        var v = /[?&]variant=(\w+)/.exec(window.location.search);
        decide(m[1].toLowerCase(), v ? v[1] : null);
        return;
      }
      if (!window.fetch) return;
      fetch("/api/livekit/config", { cache: "no-store" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (cfg) { decide(cfg && String(cfg.avatar_renderer || "").toLowerCase(), cfg && cfg.avatar_variant); })
        .catch(function () { /* route missing or network down: legacy */ });
    } catch (err) { /* never throw out of the loader */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", begin);
  else begin();
})();
