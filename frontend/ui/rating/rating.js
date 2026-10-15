// Five star rating window shown when a chat or voice session finishes.
//
//   SLTRating.ask({channel, language, turns})  Promise, resolves when the window closes (or at once)
//        {rated:true, rating}                  the visitor sent a rating
//        {rated:false, reason:"skipped"}       Skip, Escape or a click on the backdrop
//        {rated:false, reason:"no_turns"}      fewer than one turn
//        {rated:false, reason:"already_asked"} this page session already rated or skipped on this channel
//        {rated:false, reason:"disabled"}      GET /feedback/config says enabled false
//        {rated:false, reason:"open"}          a window is already open
//        {rated:false, reason:"send_failed", rating}  the POST failed, the window said thank you anyway
//   SLTRating.hide()                           close at once, no request
//
// Event on document: slt:rating-closed with the same detail as the Promise.
//
// Plain script, no dependencies, nothing stored in the browser. Strings come
// from SltI18n (keys rating.*) with English fallbacks here. Only the star
// number, the channel, the language and the turn count are sent.
(function () {
  "use strict";

  var FALLBACK = {
    "rating.title": "How was your experience?",
    "rating.line.chat": "Tell us how this chat went.",
    "rating.line.voice": "Tell us how this voice call went.",
    "rating.group": "Rating",
    "rating.star": "{n} of 5 stars",
    "rating.label.1": "Poor",
    "rating.label.2": "Fair",
    "rating.label.3": "Good",
    "rating.label.4": "Very good",
    "rating.label.5": "Excellent",
    "rating.choose": "Choose a star",
    "rating.submit": "Submit",
    "rating.skip": "Skip",
    "rating.thanks": "Thank you for your feedback",
    "rating.failed": "Could not send, thank you anyway",
  };
  var THANKS_MS = 1400;
  var FAILED_MS = 2000;
  var SEND_TIMEOUT_MS = 8000;
  var STAR_PATH = "M12 2.6l2.9 6.1 6.7.9-4.9 4.6 1.2 6.6L12 17.6 6.1 20.8l1.2-6.6L2.4 9.6l6.7-.9z";

  var cur = null;
  var asked = {};          // channel -> true once rated or skipped
  var configPromise = null; // cached answer of GET /feedback/config
  var uid = 0;

  function t(key, vars) {
    var text = "";
    try {
      if (window.SltI18n && typeof window.SltI18n.t === "function") {
        var v = window.SltI18n.t(key, vars);
        if (v && v !== key) text = v;
      }
    } catch (e) { /* use the fallback */ }
    if (!text) {
      text = FALLBACK[key] || key;
      if (vars) text = text.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? vars[k] : m; });
    }
    return text;
  }

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") node.textContent = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (kid) { if (kid) node.appendChild(kid); });
    return node;
  }

  function svg(tag, attrs) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function emit(detail) {
    try { document.dispatchEvent(new CustomEvent("slt:rating-closed", { detail: detail })); } catch (e) { /* old browser */ }
  }

  function base() {
    try {
      if (typeof SLTRating.base === "string") return SLTRating.base.replace(/\/+$/, "");
      if (typeof window.backendHttpBase === "function") return String(window.backendHttpBase() || "").replace(/\/+$/, "");
    } catch (e) { /* same origin */ }
    return "";
  }

  function headers(json) {
    var h = { Accept: "application/json" };
    if (json) h["Content-Type"] = "application/json";
    try {
      if (typeof window.authHeaders === "function") {
        var extra = window.authHeaders() || {};
        Object.keys(extra).forEach(function (k) { h[k] = extra[k]; });
      }
    } catch (e) { /* the cookie is enough */ }
    return h;
  }

  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll("button:not([tabindex='-1']), a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])"), function (n) {
      return !n.disabled && n.offsetParent !== null;
    });
  }

  function enabled() {
    if (!configPromise) {
      configPromise = new Promise(function (resolve) {
        try {
          fetch(base() + "/feedback/config", { credentials: "include", headers: headers(false) }).then(function (res) {
            if (!res.ok) return null;
            return res.json();
          }).then(function (body) {
            resolve(!(body && body.enabled === false));
          }, function () {
            configPromise = null; // try again next time
            resolve(true);
          });
        } catch (e) {
          configPromise = null;
          resolve(true);
        }
      });
    }
    return configPromise;
  }

  function checkIcon() {
    try {
      if (window.SltUi && typeof window.SltUi.icon === "function") {
        var icon = window.SltUi.icon("circle-check", { size: 40 });
        if (icon) { icon.classList.add("slt-rating-check"); return icon; }
      }
    } catch (e) { /* use the inline one */ }
    var s = svg("svg", { viewBox: "0 0 24 24", width: "40", height: "40", "aria-hidden": "true", class: "slt-rating-check", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" });
    s.appendChild(svg("circle", { cx: "12", cy: "12", r: "10" }));
    s.appendChild(svg("path", { d: "M8 12.5l3 3 5-6" }));
    return s;
  }

  function close(detail) {
    var s = cur;
    if (!s) return;
    cur = null;
    clearTimeout(s.closer);
    if (s.abort) { try { s.abort.abort(); } catch (e) { /* nothing to abort */ } }
    document.removeEventListener("keydown", s.onKey, true);
    try { if (window.SltUi && window.SltUi.modal && window.SltUi.modal.remove) window.SltUi.modal.remove(s.overlay); } catch (e) { /* decoration only */ }
    if (s.overlay.parentNode) s.overlay.parentNode.removeChild(s.overlay);
    document.body.style.overflow = s.overflow;
    var back = s.previous;
    if (back && typeof back.focus === "function" && document.contains(back)) {
      try { back.focus(); } catch (e) { /* element is gone */ }
    }
    emit(detail);
    s.resolve(detail);
  }

  function ask(opts) {
    try {
      opts = opts || {};
      var channel = opts.channel === "voice" ? "voice" : "chat";
      var turns = Math.floor(Number(opts.turns) || 0);
      if (turns < 1) return Promise.resolve({ rated: false, reason: "no_turns" });
      if (asked[channel]) return Promise.resolve({ rated: false, reason: "already_asked" });
      if (cur) return Promise.resolve({ rated: false, reason: "open" });
      cur = { pending: true }; // blocks a second ask while the config request runs
      var token = cur;
      return enabled().then(function (on) {
        if (cur !== token) return { rated: false, reason: "open" };
        cur = null;
        if (!on) return { rated: false, reason: "disabled" };
        if (asked[channel]) return { rated: false, reason: "already_asked" };
        return new Promise(function (resolve) {
          try { build(channel, String(opts.language || ""), turns, resolve); }
          catch (e) { cur = null; resolve({ rated: false, reason: "error" }); }
        });
      }, function () {
        if (cur === token) cur = null;
        return { rated: false, reason: "error" };
      });
    } catch (e) {
      return Promise.resolve({ rated: false, reason: "error" });
    }
  }

  function build(channel, language, turns, resolve) {
    var n = ++uid;
    var titleId = "slt-rating-title-" + n;
    var lineId = "slt-rating-line-" + n;
    var rating = 0;
    var hover = 0;
    var starNodes = [];

    var group = el("div", { class: "slt-rating-stars", role: "radiogroup", "aria-labelledby": titleId });
    var live = el("p", { class: "slt-rating-label", "aria-live": "polite", text: t("rating.choose") });

    function paint() {
      var shown = hover || rating;
      starNodes.forEach(function (node, i) {
        var v = i + 1;
        node.classList.toggle("is-on", v <= shown);
        node.classList.toggle("is-preview", !!hover && v <= hover);
        node.setAttribute("aria-checked", v === rating ? "true" : "false");
        node.setAttribute("tabindex", (rating ? v === rating : v === 1) ? "0" : "-1");
      });
      live.textContent = shown ? t("rating.label." + shown) : t("rating.choose");
      submit.disabled = !rating;
    }

    function choose(v, focus) {
      rating = Math.max(1, Math.min(5, v));
      hover = 0;
      paint();
      if (focus) starNodes[rating - 1].focus();
    }

    for (var i = 1; i <= 5; i++) {
      (function (v) {
        var icon = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false", class: "slt-rating-icon" });
        icon.appendChild(svg("path", { d: STAR_PATH }));
        var star = el("button", { type: "button", class: "slt-rating-star", role: "radio", "aria-checked": "false", "aria-label": t("rating.star", { n: window.SltFormat ? window.SltFormat.integer(v) : v }), tabindex: "-1" }, [icon]);
        star.addEventListener("click", function () { choose(v, false); });
        star.addEventListener("mouseenter", function () { hover = v; paint(); });
        star.addEventListener("mouseleave", function () { hover = 0; paint(); });
        star.addEventListener("focus", function () { hover = 0; focusPreview = v; paint2(); });
        star.addEventListener("blur", function () { focusPreview = 0; paint2(); });
        starNodes.push(star);
        group.appendChild(star);
      })(i);
    }
    // Focus previews like hover but does not change the answer.
    var focusPreview = 0;
    function paint2() {
      var saved = hover;
      if (!hover && focusPreview) hover = focusPreview;
      paint();
      hover = saved;
    }

    group.addEventListener("keydown", function (ev) {
      var idx = starNodes.indexOf(document.activeElement);
      if (idx < 0) return;
      var next = null;
      if (ev.key === "ArrowRight" || ev.key === "ArrowUp") next = Math.min(5, idx + 2);
      else if (ev.key === "ArrowLeft" || ev.key === "ArrowDown") next = Math.max(1, idx);
      else if (ev.key === "Home") next = 1;
      else if (ev.key === "End") next = 5;
      else if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); choose(idx + 1, false); return; }
      if (next == null) return;
      ev.preventDefault();
      choose(next, true);
    });

    var submit = el("button", { type: "button", class: "choice-button choice-button-solid slt-v-btn slt-rating-submit", text: t("rating.submit") });
    var skip = el("button", { type: "button", class: "slt-rating-skip", text: t("rating.skip") });
    submit.disabled = true;
    var message = el("p", { class: "slt-rating-message hidden", role: "status", "aria-live": "polite" });
    var actions = el("div", { class: "slt-rating-actions" }, [submit, skip]);
    var form = el("div", { class: "slt-rating-form" }, [
      el("p", { class: "slt-rating-line", id: lineId, text: t("rating.line." + channel) }),
      group,
      live,
      actions,
    ]);
    var card = el("div", {
      class: "card slt-rating-card",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
      "aria-describedby": lineId,
      tabindex: "-1",
    }, [
      el("h2", { class: "slt-v-title slt-rating-title", id: titleId, text: t("rating.title") }),
      form,
      message,
    ]);
    var overlay = el("div", { class: "slt-rating-overlay" }, [card]);

    var s = cur = {
      overlay: overlay,
      closer: null,
      abort: null,
      busy: false,
      done: false,
      resolve: resolve,
      previous: document.activeElement,
      overflow: document.body.style.overflow,
    };
    asked[channel] = true; // one prompt per page session and channel, whatever the visitor does

    function skipNow() {
      if (cur !== s || s.busy || s.done) return;
      close({ rated: false, reason: "skipped" });
    }

    function afterSend(ok) {
      if (cur !== s) return;
      s.busy = false;
      s.done = true;
      form.classList.add("hidden");
      message.textContent = "";
      message.classList.remove("hidden");
      if (ok) {
        message.appendChild(checkIcon());
        message.appendChild(el("span", { text: t("rating.thanks") }));
        message.classList.add("is-ok");
      } else {
        message.textContent = t("rating.failed");
        message.classList.add("is-failed");
      }
      var detail = ok ? { rated: true, rating: rating } : { rated: false, reason: "send_failed", rating: rating };
      s.closer = setTimeout(function () { close(detail); }, ok ? THANKS_MS : FAILED_MS);
    }

    function send() {
      if (cur !== s || s.busy || s.done || !rating) return;
      s.busy = true;
      submit.disabled = true;
      skip.disabled = true;
      card.setAttribute("aria-busy", "true");
      var body = JSON.stringify({ rating: rating, channel: channel, language: language, turns: turns });
      var timer = null;
      try {
        if (typeof AbortController === "function") {
          s.abort = new AbortController();
          timer = setTimeout(function () { try { s.abort.abort(); } catch (e) { /* done */ } }, SEND_TIMEOUT_MS);
        }
        fetch(base() + "/feedback/rating", {
          method: "POST",
          credentials: "include",
          headers: headers(true),
          body: body,
          signal: s.abort ? s.abort.signal : undefined,
        }).then(function (res) {
          clearTimeout(timer);
          afterSend(!!res.ok);
        }, function () {
          clearTimeout(timer);
          afterSend(false);
        });
      } catch (e) {
        clearTimeout(timer);
        afterSend(false);
      }
    }

    submit.addEventListener("click", send);
    skip.addEventListener("click", skipNow);
    var downOnBackdrop = false;
    overlay.addEventListener("mousedown", function (ev) { downOnBackdrop = ev.target === overlay; });
    overlay.addEventListener("click", function (ev) {
      if (ev.target === overlay && downOnBackdrop) skipNow();
      downOnBackdrop = false;
    });
    s.onKey = function (ev) {
      if (cur !== s) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        if (s.done) close({ rated: false, reason: "skipped" }); // never leave it stuck
        else skipNow();
      } else if (ev.key === "Tab") {
        var items = focusables(card);
        if (!items.length) { ev.preventDefault(); card.focus(); return; }
        var first = items[0], last = items[items.length - 1], active = document.activeElement;
        if (ev.shiftKey && (active === first || active === card || !card.contains(active))) { ev.preventDefault(); last.focus(); }
        else if (!ev.shiftKey && (active === last || !card.contains(active))) { ev.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", s.onKey, true);

    document.body.appendChild(overlay);
    document.body.style.overflow = "hidden";
    try { if (window.SltUi && window.SltUi.modal && window.SltUi.modal.enhance) window.SltUi.modal.enhance(overlay, { icon: "message-square" }); } catch (e) { /* decoration only */ }
    paint();
    card.focus();
  }

  function hide() {
    if (cur && cur.overlay) close({ rated: false, reason: "skipped" });
  }

  var SLTRating = { ask: ask, hide: hide, base: null };
  window.SLTRating = SLTRating;
})();
