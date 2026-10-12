/*
 * SltUi.dashboard: narrow layer for the voice screen (styles: dashboard.css).
 * attach() MOVES the existing buttons (no clones) into a bottom bar and a More sheet
 * up to 1023 px; detach() or a wider viewport puts every element back. Also: avatar
 * ambience, a mode chip, empty transcript states.
 */
(function () {
  "use strict";

  var NARROW = "(max-width: 1023.98px)";
  var FB = {
    more: "More", captions: "Captions", mic: "Mic", end: "End", history: "History",
    voice: "Voice and avatar", logout: "Log out", theme: "Theme", sound: "Intro sound",
    sheet_close: "Close", mode: "Mode"
  };
  var EMPTY = "Your conversation will appear here as it happens.";
  var STATES = ["idle", "listening", "thinking", "speaking", "error"];
  var ICONS = {
    more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
    close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
  };

  var S = null;                      // attached instance
  var cur = "idle", modeText = "", level = 0, levelAt = -1e9;

  function $(id) { return document.getElementById(id); }
  function tr(k) {                     // "more" is dashboard.more
    var full = k === "empty" ? "empty.transcript" : "dashboard." + k, v = "";
    try { v = window.SltI18n ? window.SltI18n.t(full) : ""; } catch (e) { v = ""; }
    return v && v !== full ? v : FB[k] || EMPTY;
  }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name, size) {
    var w = el("span");
    w.innerHTML = '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size + '" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true">' + ICONS[name] + "</svg>";
    return w.firstChild;
  }
  function at(n, o) { for (var k in o) n.setAttribute(k, o[k]); return n; }
  function reduced() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function on(t, type, fn) {
    t.addEventListener(type, fn);
    S.off.push(function () { t.removeEventListener(type, fn); });
  }
  function click(id, fn) { on(S[id], "click", fn); }
  function labelFor(key, cls, act) {
    var l = el("span", cls);
    l.setAttribute("data-k", key);
    l.addEventListener("click", act);
    return l;
  }

  function park(node, dest) {
    if (!node || !node.parentNode) return;
    var ph = document.createComment("dash-origin");
    node.parentNode.insertBefore(ph, node);
    S.moved.push({ el: node, ph: ph });
    dest.appendChild(node);
  }
  function unpark() {
    S.moved.slice().reverse().forEach(function (m) {
      if (m.ph.parentNode) m.ph.parentNode.replaceChild(m.el, m.ph);
    });
    S.moved = [];
  }
  function cell(btn, key, cls, labelCls, wrapCls) {
    var w = el("div", wrapCls);
    if (btn) park(btn, w);
    w.appendChild(labelFor(key, labelCls, function () { if (btn) btn.click(); }));
    if (cls) w.className += " " + cls;
    return w;
  }

  function layerIn() {
    S.bar.innerHTML = "";
    [["captions-toggle", "captions", ""], ["mic-toggle", "mic", "dash-item-mic"],
      ["end-chat-button", "end", ""]].forEach(function (c) {
      S.bar.appendChild(cell($(c[0]), c[1], c[2], "dash-label", "dash-item"));
    });
    S.bar.appendChild(S.moreItem);
    S.rows.innerHTML = "";
    [["history-toggle", "history"], ["gender-toggle", "voice"],
      ["logout-button", "logout"], ["theme-toggle", "theme"]].forEach(function (c) {
      S.rows.appendChild(cell($(c[0]), c[1], "", "dash-row-label", "dash-row"));
    });
    S.rows.appendChild(S.soundRow);
    S.screen.classList.add("dash-mobile");
    document.documentElement.classList.add("dash-voice");
    S.bar.hidden = false;
    if ($("history-panel")) $("history-panel").classList.add("dash-hist");
    relabel();
  }
  function layerOut() {
    closeSheet(false);
    unpark();
    S.bar.hidden = true;
    S.screen.classList.remove("dash-mobile");
    document.documentElement.classList.remove("dash-voice");
    if ($("history-panel")) $("history-panel").classList.remove("dash-hist");
  }
  function sync() {
    if (!S) return;
    var want = S.mq.matches && S.screen.classList.contains("active");
    if (want && !S.mobile) { S.mobile = true; layerIn(); measure(); }
    else if (!want && S.mobile) { S.mobile = false; layerOut(); }
    kick();
  }
  function measure() {
    var r = S.bar.getBoundingClientRect();
    S.screen.style.setProperty("--dash-bar-h", Math.ceil(r.height) + "px");
    S.screen.style.setProperty("--dash-bar-w", Math.ceil(r.width) + "px");
  }
  function relabel() {
    if (!S) return;
    [S.bar, S.sheet].forEach(function (root) {
      Array.prototype.forEach.call(root.querySelectorAll("[data-k]"), function (n) {
        n.textContent = tr(n.getAttribute("data-k"));
      });
    });
    S.title.textContent = tr("more");
    [[S.moreBtn, "more"], [S.sheet, "more"], [S.closeBtn, "sheet_close"], [S.soundBtn, "sound"]]
      .forEach(function (p) { p[0].setAttribute("aria-label", tr(p[1])); });
    S.chipLabel.textContent = tr("mode");
    S.titled.forEach(function (b) { b.title = b.getAttribute("aria-label") || ""; });
    decorate($("transcript"), true);
    if (S.mobile) measure();
  }

  function toggleSheet(open, restore) {
    if (!S || S.open === open) return;
    S.open = open;
    if (open) S.lastFocus = document.activeElement;
    S.sheet.classList.toggle("open", open);
    S.backdrop.classList.toggle("open", open);
    S.moreBtn.setAttribute("aria-expanded", String(open));
    if (open) S.closeBtn.focus();
    else if (restore !== false && S.lastFocus && S.lastFocus.focus) S.lastFocus.focus();
  }
  function openSheet() { toggleSheet(true); }
  function closeSheet(restore) { toggleSheet(false, restore); }
  function onKey(e) {
    var hp = $("history-panel"), c = $("close-history");
    if (e.key === "Escape") {
      if (S.open) { closeSheet(); e.preventDefault(); }
      else if (S.mobile && hp && c && !hp.classList.contains("hidden")) c.click();
    } else if (e.key === "Tab" && S.open) {   // focus trap
      var f = Array.prototype.filter.call(S.sheet.querySelectorAll("button"),
        function (n) { return n.offsetParent !== null; });
      var first = f[0], last = f[f.length - 1], a = document.activeElement;
      if (!f.length) return;
      if (!S.sheet.contains(a)) { first.focus(); e.preventDefault(); }
      else if (e.shiftKey && a === first) { last.focus(); e.preventDefault(); }
      else if (!e.shiftKey && a === last) { first.focus(); e.preventDefault(); }
    }
  }
  function soundOn() {
    try { return localStorage.getItem("slt-sound") === "on"; } catch (e) { return false; }
  }
  function setSound(v) {
    try { localStorage.setItem("slt-sound", v ? "on" : "off"); } catch (e) { /* storage blocked */ }
    S.soundBtn.setAttribute("aria-checked", String(v));
    document.dispatchEvent(new CustomEvent("slt:sound", { detail: { on: v } }));
  }

  function setState(st) {
    if (STATES.indexOf(st) < 0) st = "idle";
    cur = st;
    if (!S) return;
    S.halo.className = "dash-halo dash-st-" + st + (document.hidden ? " dash-paused" : "");
    S.screen.setAttribute("data-dash-state", st);
    [S.ring1, S.ring2, S.glow].forEach(function (n) { n.style.transform = ""; n.style.opacity = ""; });
    if (st === "error" && !reduced() && S.bot && S.bot.animate) {
      var x = function (v) { return { transform: "translateX(" + v + "px)" }; };
      S.bot.animate([x(0), x(-6), x(6), x(-6), x(6), x(0)], { duration: 300, easing: "ease-out" });
    }
    kick();
  }
  function setLevel(v) {
    level = v > 0 ? Math.min(v, 1) : 0;
    levelAt = performance.now();
  }
  function setMode(text) {
    modeText = text = text == null ? "" : String(text);
    if (!S || S.chipText === text) return;
    S.chipText = text;
    var old = S.chipBox.querySelector(".dash-chip-in");
    if (old) {
      old.className = "dash-chip-text dash-chip-out";
      setTimeout(function () { if (old.parentNode) old.parentNode.removeChild(old); }, 260);
    }
    S.chipBox.appendChild(el("span", "dash-chip-text dash-chip-in", text));
    S.chip.classList.toggle("has-text", !!text);
  }
  function wantLoop() {
    return S && S.mobile && !document.hidden && !reduced() && (cur === "listening" || cur === "speaking");
  }
  function kick() {
    if (!S) return;
    S.halo.classList.toggle("dash-paused", !!document.hidden);
    if (wantLoop() && !S.raf) S.raf = requestAnimationFrame(tick);
  }
  function tick(t) {                   // about 30 fps, transform and opacity only
    S.raf = 0;
    if (!wantLoop()) return;
    if (t - S.last >= 33) {
      S.last = t;
      var target = t - levelAt < 600 ? level
        : cur === "speaking" ? 0.55 + 0.3 * Math.sin(t / 380) : 0.3 + 0.2 * Math.sin(t / 700);
      S.smooth += (target - S.smooth) * 0.4;
      var s = S.smooth;
      if (cur === "listening") {
        S.ring1.style.transform = "scale(" + (1 + 0.14 * s) + ")";
        S.ring2.style.transform = "scale(" + (1 + 0.3 * s) + ")";
        S.ring2.style.opacity = 0.15 + 0.6 * s;
      } else {
        S.glow.style.opacity = 0.25 + 0.75 * s;
      }
    }
    S.raf = requestAnimationFrame(tick);
  }

  function decorate(box, translate) {
    if (!box) return;
    var h = box.children[0], only = box.children.length === 1 && h.classList.contains("hint");
    box.classList.toggle("dash-empty", only);
    if (!only) return;
    if (translate) {
      if (h.getAttribute("data-dash-text") == null) h.setAttribute("data-dash-text", h.textContent);
      if (h.textContent !== tr("empty")) h.textContent = tr("empty");
    }
    if (h.querySelector(".dash-empty-ico")) return;
    h.classList.add("dash-empty-hint");
    h.insertBefore(add(el("span", "dash-empty-ico"), icon("chat", 28)).parentNode, h.firstChild);
    add(h, at(el("span", "dash-skel"), { "aria-hidden": "true" })).innerHTML = "<i></i><i></i><i></i>";
  }
  function undecorate(box) {
    box.classList.remove("dash-empty");
    var h = box.querySelector(".hint");
    if (!h) return;
    Array.prototype.forEach.call(h.querySelectorAll(".dash-empty-ico, .dash-skel"), function (n) { n.remove(); });
    h.classList.remove("dash-empty-hint");
    var orig = h.getAttribute("data-dash-text");
    if (orig != null) { h.textContent = orig; h.removeAttribute("data-dash-text"); }
  }

  function add(p, c) { p.appendChild(c); return c; }
  function build() {
    S.bar = el("nav", "dash-bar");
    S.bar.hidden = true;
    S.moreBtn = at(el("button", "dash-more"), { type: "button", "aria-haspopup": "dialog", "aria-expanded": "false" });
    add(S.moreBtn, icon("more", 24));
    S.moreItem = el("div", "dash-item");
    add(S.moreItem, S.moreBtn);
    add(S.moreItem, labelFor("more", "dash-label", openSheet));

    S.backdrop = el("div", "dash-backdrop");
    S.sheet = at(el("div", "dash-sheet"), { role: "dialog", "aria-modal": "true" });
    var head = add(S.sheet, el("div", "dash-sheet-head"));
    S.title = add(head, el("h2", "dash-sheet-title"));
    S.closeBtn = at(add(head, el("button", "dash-close")), { type: "button" });
    add(S.closeBtn, icon("close", 20));
    S.rows = add(S.sheet, el("div", "dash-rows"));
    S.soundRow = el("div", "dash-row");
    S.soundBtn = at(add(S.soundRow, el("button", "dash-switch")),
      { type: "button", role: "switch", "aria-checked": String(soundOn()) });
    add(S.soundBtn, el("span", "dash-knob"));
    add(S.soundRow, labelFor("sound", "dash-row-label", function () { S.soundBtn.click(); }));
    [S.bar, S.backdrop, S.sheet].forEach(function (n) { add(document.body, n); });

    S.chip = at(el("div", "dash-mode"), { role: "status" });
    S.chipLabel = add(S.chip, el("span", "dash-mode-label"));
    S.chipBox = add(S.chip, el("span", "dash-chip-box"));
    var logo = S.screen.querySelector(".voice-logo");
    S.screen.insertBefore(S.chip, logo ? logo.nextSibling : null);

    S.bot = $("bot-avatar");
    S.halo = at(el("div", "dash-halo"), { "aria-hidden": "true" });
    S.ring1 = el("span", "dash-ring dash-ring1");
    S.ring2 = el("span", "dash-ring dash-ring2");
    S.glow = el("span", "dash-glow");
    [S.glow, S.ring2, S.ring1, el("span", "dash-arc")].forEach(function (n) { add(S.halo, n); });
    if (S.bot) add(S.bot, S.halo);
    S.titled = Array.prototype.slice.call(S.screen.querySelectorAll("button"))
      .concat($("theme-toggle") || []).filter(function (b) { return !b.title; });
  }

  function attach(screen) {
    try {
      screen = screen || $("screen-voice");
      if (!screen) return false;
      if (S) { if (S.screen === screen) { sync(); return true; } detach(); }
      S = { screen: screen, moved: [], off: [], obs: [], boxes: [], mobile: false, open: false,
        raf: 0, last: 0, smooth: 0, chipText: null, mq: window.matchMedia(NARROW) };
      screen.classList.add("dash-on");
      build();

      click("moreBtn", openSheet);
      click("closeBtn", function () { closeSheet(); });
      click("backdrop", function () { closeSheet(); });
      click("soundBtn", function () { setSound(S.soundBtn.getAttribute("aria-checked") !== "true"); });
      click("rows", function (e) {
        if (e.target.closest("#history-toggle, #logout-button")) closeSheet(false);
      });
      on(document, "keydown", onKey);
      on(document, "visibilitychange", kick);
      on(window, "resize", function () { if (S.mobile) measure(); });
      if ($("history-panel")) on($("history-panel"), "click", function (e) {
        if (S.mobile && e.target === this && $("close-history")) $("close-history").click();
      });
      on(S.mq, "change", sync);

      var watch = function (target, opts, fn) {
        if (!target) return;
        var o = new MutationObserver(fn);
        o.observe(target, opts);
        S.obs.push(o);
      };
      var botState = function () {
        var m = S.bot && /\bstate-(\w+)/.exec(S.bot.className);
        return m && m[1] !== "calibrating" ? m[1] : "idle";
      };
      watch(S.bot, { attributes: true, attributeFilter: ["class"] }, function () { setState(botState()); });
      watch(screen, { attributes: true, attributeFilter: ["class"] }, sync);
      watch(document.documentElement, { attributes: true, attributeFilter: ["lang"] }, relabel);
      ["transcript", "chat-transcript"].forEach(function (id) {
        var box = $(id), tl = id === "transcript";
        if (!box) return;
        S.boxes.push(box);
        decorate(box, tl);
        watch(box, { childList: true }, function () { decorate(box, tl); });
      });

      setState(S.bot ? botState() : cur);
      setMode(modeText);
      sync();
      relabel();
      return true;
    } catch (err) {
      console.warn("SltUi.dashboard.attach failed", err);
      try { detach(); } catch (e2) { /* nothing to undo */ }
      return false;
    }
  }

  function detach() {
    if (!S) return;
    var s = S;
    if (s.raf) cancelAnimationFrame(s.raf);
    s.obs.forEach(function (o) { o.disconnect(); });
    s.off.forEach(function (f) { f(); });
    if (s.mobile) layerOut();
    unpark();
    s.boxes.forEach(undecorate);
    (s.titled || []).forEach(function (b) { b.removeAttribute("title"); });
    [s.bar, s.backdrop, s.sheet, s.chip, s.halo].forEach(function (n) {
      if (n && n.parentNode) n.parentNode.removeChild(n);
    });
    s.screen.classList.remove("dash-on", "dash-mobile");
    s.screen.removeAttribute("data-dash-state");
    ["h", "w"].forEach(function (d) { s.screen.style.removeProperty("--dash-bar-" + d); });
    document.documentElement.classList.remove("dash-voice");
    S = null;
  }

  window.SltUi = window.SltUi || {};
  window.SltUi.dashboard = {
    attach: attach, detach: detach, setState: setState, setLevel: setLevel,
    setMode: setMode, soundOn: soundOn
  };
})();
