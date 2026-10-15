// Confirmation modal for actions that change an account (plan section 7).
//
//   SLTConfirm.show(confirmation)   open the modal for {id, tool, title, lines, expires_in, state}
//   SLTConfirm.hide()               close it at once, no request, no result event
//   SLTConfirm.resolve(kind, text)  the answer is known (spoken yes handled by the server):
//                                   show the text, fire the result event, close shortly after
//   SLTConfirm.setStatus(text|null) a small aria-live status line, "Checking your balance"
//
// Events on document (detail never holds the token):
//   slt:confirmation-result  {kind, text}   kind: executed, failed, cancelled, expired, invalid, disabled, refused
//   slt:confirmation-closed  {}
//
// Plain script, no dependencies. Strings come from SltI18n (keys confirm.ui.*)
// with English fallbacks here. The modal uses the existing card, title and
// button classes of style.css and verify.css.
(function () {
  "use strict";

  var FALLBACK = {
    "confirm.ui.confirm": "Confirm",
    "confirm.ui.cancel": "Cancel",
    "confirm.ui.expires": "Expires in {time}",
    "confirm.ui.expired": "This request timed out. Nothing was changed.",
    "confirm.ui.cancelled": "Cancelled. Nothing was changed.",
    "confirm.ui.invalid": "This request is no longer valid. Nothing was changed.",
    "confirm.ui.unknown": "I could not tell whether it went through. Please check your account before trying again.",
    "confirm.ui.busy": "Working",
    "confirm.ui.hint": "Check the details, then confirm to go ahead.",
  };
  var CLOSE_DELAY = { executed: 2800, failed: 3500, expired: 2000, cancelled: 1200, invalid: 2000, disabled: 2500, refused: 2500 };

  var cur = null; // the open modal's state
  var statusBox = null;
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

  function emit(name, detail) {
    try { document.dispatchEvent(new CustomEvent(name, { detail: detail || {} })); } catch (e) { /* old browser */ }
  }

  function base() {
    try {
      if (typeof SLTConfirm.base === "string") return SLTConfirm.base.replace(/\/+$/, "");
      if (typeof window.backendHttpBase === "function") return String(window.backendHttpBase() || "").replace(/\/+$/, "");
    } catch (e) { /* same origin */ }
    return "";
  }

  function headers() {
    var h = { Accept: "application/json" };
    try {
      if (typeof window.authHeaders === "function") {
        var extra = window.authHeaders() || {};
        Object.keys(extra).forEach(function (k) { h[k] = extra[k]; });
      }
    } catch (e) { /* the cookie is enough */ }
    return h;
  }

  function mmss(seconds) {
    var s = Math.max(0, Math.ceil(seconds));
    if (window.SltFormat) return window.SltFormat.duration(s);
    return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + (s % 60);
  }

  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll("button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])"), function (n) {
      return !n.disabled && n.offsetParent !== null;
    });
  }

  // Opening and closing

  function close() {
    var s = cur;
    if (!s) return;
    cur = null;
    clearInterval(s.timer);
    clearTimeout(s.closer);
    document.removeEventListener("keydown", s.onKey, true);
    try { if (window.SltUi && window.SltUi.modal && window.SltUi.modal.remove) window.SltUi.modal.remove(s.overlay); } catch (e) { /* decoration only */ }
    if (s.overlay.parentNode) s.overlay.parentNode.removeChild(s.overlay);
    document.body.style.overflow = s.overflow;
    var back = s.previous;
    if (back && typeof back.focus === "function" && document.contains(back)) {
      try { back.focus(); } catch (e) { /* element is gone */ }
    }
    emit("slt:confirmation-closed", {});
  }

  function show(confirmation) {
    try {
      if (!confirmation || typeof confirmation.id !== "string" || !confirmation.id) return;
      if (cur && cur.id === confirmation.id) return;
      if (cur) close();
      build(confirmation);
    } catch (e) { /* never break the page */ }
  }

  function build(c) {
    var n = ++uid;
    var titleId = "slt-confirm-title-" + n;
    var hintId = "slt-confirm-hint-" + n;
    var lines = Array.isArray(c.lines) ? c.lines : [];

    var list = el("dl", { class: "slt-confirm-lines" });
    lines.forEach(function (line) {
      list.appendChild(el("div", { class: "slt-confirm-row" }, [
        el("dt", { text: String(line.label == null ? "" : line.label) }),
        el("dd", { text: String(line.value == null ? "" : line.value) }),
      ]));
    });
    var timer = el("p", { class: "slt-confirm-timer" });
    var result = el("p", { class: "slt-confirm-result hidden", role: "status", "aria-live": "polite" });
    var yes = el("button", { type: "button", class: "choice-button choice-button-solid slt-v-btn slt-confirm-yes", text: t("confirm.ui.confirm") });
    var no = el("button", { type: "button", class: "choice-button choice-button-outline slt-v-btn slt-confirm-no", text: t("confirm.ui.cancel") });
    var actions = el("div", { class: "slt-confirm-actions" }, [yes, no]);
    var card = el("div", {
      class: "card slt-confirm-card",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
      "aria-describedby": hintId,
      tabindex: "-1",
    }, [
      el("h2", { class: "slt-v-title slt-confirm-title", id: titleId, text: String(c.title || "") }),
      el("p", { class: "slt-confirm-hint", id: hintId, text: t("confirm.ui.hint") }),
      list,
      timer,
      result,
      actions,
    ]);
    var overlay = el("div", { class: "slt-confirm-overlay" }, [card]);

    var s = cur = {
      id: c.id,
      overlay: overlay,
      card: card,
      timer: null,
      closer: null,
      busy: false,
      done: false,
      previous: document.activeElement,
      overflow: document.body.style.overflow,
      endAt: Date.now() + Math.max(1, Number(c.expires_in) || 60) * 1000,
      shown: -1,
    };

    function tick() {
      var left = Math.max(0, (s.endAt - Date.now()) / 1000);
      var whole = Math.ceil(left);
      if (whole !== s.shown) {
        s.shown = whole;
        timer.textContent = t("confirm.ui.expires", { time: mmss(left) });
      }
      if (left <= 0 && !s.done && !s.busy) finish("expired", t("confirm.ui.expired"));
    }

    function finish(kind, text) {
      if (cur !== s || s.done) return;
      s.done = true;
      clearInterval(s.timer);
      list.classList.add("hidden");
      timer.classList.add("hidden");
      actions.classList.add("hidden");
      result.textContent = text;
      result.classList.remove("hidden");
      card.removeAttribute("aria-busy");
      emit("slt:confirmation-result", { kind: kind, text: text });
      s.closer = setTimeout(function () { if (cur === s) close(); }, CLOSE_DELAY[kind] || 2500);
    }
    s.finish = finish;

    function send(path, onJson, onFail) {
      s.busy = true;
      yes.disabled = no.disabled = true;
      card.setAttribute("aria-busy", "true");
      var url = base() + "/confirm/" + encodeURIComponent(c.id) + "/" + path;
      fetch(url, { method: "POST", credentials: "include", headers: headers() }).then(function (res) {
        if (res.status === 404) return { kind: "invalid", text: t("confirm.ui.invalid") };
        return res.json().then(function (body) {
          if (!res.ok) return { kind: "invalid", text: t("confirm.ui.invalid") };
          return body;
        });
      }).then(function (body) {
        s.busy = false;
        onJson(body || {});
      }, function () {
        s.busy = false;
        onFail();
      });
    }

    function accept() {
      if (s.busy || s.done) return;
      send("accept", function (body) {
        finish(String(body.kind || "failed"), String(body.text || ""));
      }, function () {
        finish("failed", t("confirm.ui.unknown")); // never say "nothing changed" when we cannot know
      });
    }

    function cancel() {
      if (s.busy || s.done) return;
      send("cancel", function (body) {
        finish(body.kind === "cancelled" ? "cancelled" : String(body.kind || "cancelled"), String(body.text || t("confirm.ui.cancelled")));
      }, function () {
        finish("cancelled", t("confirm.ui.cancelled"));
      });
    }

    yes.addEventListener("click", accept);
    no.addEventListener("click", cancel);
    s.onKey = function (ev) {
      if (cur !== s) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        cancel();
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
    try { if (window.SltUi && window.SltUi.modal && window.SltUi.modal.enhance) window.SltUi.modal.enhance(overlay, { icon: "shield-check" }); } catch (e) { /* decoration only */ }
    tick();
    s.timer = setInterval(tick, 250);
    card.focus(); // the Confirm button is deliberately not focused
  }

  function hide() {
    close();
  }

  function resolve(kind, text) {
    var message = String(text || "");
    if (cur && !cur.done) {
      cur.finish(String(kind || "executed"), message);
    } else if (!cur) {
      emit("slt:confirmation-result", { kind: String(kind || "executed"), text: message });
    }
  }

  // The status line sits above the bottom bar, outside the app's own markup.
  function setStatus(text) {
    try {
      if (!statusBox) {
        statusBox = el("div", { id: "slt-confirm-status", class: "slt-confirm-status hidden", role: "status", "aria-live": "polite", "aria-atomic": "true" });
        document.body.appendChild(statusBox);
      }
      if (text) {
        statusBox.textContent = String(text);
        statusBox.classList.remove("hidden");
      } else {
        statusBox.classList.add("hidden");
        statusBox.textContent = "";
      }
    } catch (e) { /* decoration only */ }
  }

  var SLTConfirm = { show: show, hide: hide, resolve: resolve, setStatus: setStatus, base: null };
  window.SLTConfirm = SLTConfirm;
})();
