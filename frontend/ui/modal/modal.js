// Modal polish for window.SltUi (docs/UI_PLAN.md sections 3 and 8).
//   SltUi.modal.enhance(overlay, {icon, tone})   decorate an existing modal
//   SltUi.modal.remove(overlay) / setBadge(overlay, icon, tone)
//   SltUi.modal.wizardProgress(container, {current, total, label(c, t)})
//   SltUi.modal.transition(fromEl, toEl, "forward" | "back")   Promise
//   SltUi.modal.shake(el), success(hostEl), init(), watchWizard()
// The owner's code is never touched: open and close are seen with a
// MutationObserver. The only owner changes are the data-slt-m attribute on an
// overlay and data-filled on the code boxes. The badge needs icons.js.
(function () {
  "use strict";
  var root = (window.SltUi = window.SltUi || {});
  var handles = new WeakMap(); // overlay -> handle
  var ghosts = new WeakSet(); // closing copies, never enhanced
  var bodyObs = null;
  var EASE_OUT = "cubic-bezier(0.22, 0.8, 0.3, 1)";
  var EASE_IN = "cubic-bezier(0.4, 0, 1, 1)";
  // Blur is costly on weak phones: both numbers must be reported and low.
  var FLAT = (navigator.hardwareConcurrency || 99) <= 4 && (navigator.deviceMemory || 99) <= 2;
  var KNOWN = [
    ["#mic-permission-modal", { icon: "mic" }],
    ["#history-panel", { icon: "history" }],
    ["#agent-modal", { icon: "plug" }],
    ["#confirm-modal", { icon: "triangle-alert", tone: "danger" }],
    ["#secret-modal", { icon: "key-round" }],
  ];

  function reduced() {
    try {
      return matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }
  function emit(name, id) {
    try {
      document.dispatchEvent(new CustomEvent(name, { detail: { id: id } }));
    } catch (e) {}
  }
  function tr(key, vars) {
    try {
      var out = window.SltI18n.t(key, vars);
      return out && out !== key ? out : "";
    } catch (e) {
      return "";
    }
  }
  function isOpen(el) {
    return el.isConnected && !el.classList.contains("hidden") && !el.hasAttribute("hidden");
  }
  function each(list, fn) {
    Array.prototype.forEach.call(list, fn);
  }

  function shake(el) {
    if (!el || reduced()) return;
    el.classList.remove("slt-shake");
    requestAnimationFrame(function () {
      el.classList.add("slt-shake");
      setTimeout(function () {
        el.classList.remove("slt-shake");
      }, 320);
    });
  }

  function transition(fromEl, toEl, direction) {
    var s = direction === "back" || direction === -1 ? -1 : 1;
    var jobs = [];
    function run(el, frames, opts) {
      if (el && el.animate) jobs.push(el.animate(frames, opts).finished.catch(function () {}));
    }
    if (reduced()) {
      run(toEl, [{ opacity: 0 }, { opacity: 1 }], { duration: 150, easing: "linear" });
    } else {
      // The outgoing element stays faded out; the caller removes it.
      run(fromEl, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(" + -24 * s + "px)" }], {
        duration: 260,
        easing: EASE_IN,
        fill: "forwards",
      });
      run(toEl, [{ opacity: 0, transform: "translateX(" + 24 * s + "px)" }, { opacity: 1, transform: "none" }], {
        duration: 260,
        easing: EASE_OUT,
      });
    }
    return Promise.all(jobs).then(function () {});
  }

  function success(host, opts) {
    var n = Math.max(10, Math.min(14, (opts && opts.count) || 12));
    var NS = "http://www.w3.org/2000/svg";
    var box = document.createElement("div");
    var svg = document.createElementNS(NS, "svg");
    box.className = "slt-ok";
    box.setAttribute("aria-hidden", "true");
    svg.setAttribute("viewBox", "0 0 24 24");
    [["circle", { cx: 12, cy: 12, r: 10 }], ["path", { d: "m16 9-5.5 5.5L8 12" }]].forEach(function (p) {
      var node = document.createElementNS(NS, p[0]);
      node.setAttribute("pathLength", "1");
      for (var k in p[1]) node.setAttribute(k, p[1][k]);
      svg.appendChild(node);
    });
    box.appendChild(svg);
    if (!reduced()) {
      for (var i = 0; i < n; i++) {
        var a = (i / n) * Math.PI * 2;
        var d = 38 + (i % 2) * 8;
        var dot = document.createElement("span");
        dot.className = "slt-ok-dot";
        dot.style.setProperty("--dx", Math.round(Math.cos(a) * d) + "px");
        dot.style.setProperty("--dy", Math.round(Math.sin(a) * d) + "px");
        box.appendChild(dot);
      }
      setTimeout(function () {
        each(box.querySelectorAll(".slt-ok-dot"), function (x) {
          x.remove();
        });
      }, 500);
    }
    if (host) host.appendChild(box);
    return box;
  }

  function wizardProgress(container, o) {
    o = o || {};
    var h = container._sltProg;
    if (!h) {
      var total = Math.max(1, o.total || 3);
      var bar = document.createElement("div");
      var label = document.createElement("span");
      var segs = [];
      bar.className = "slt-prog-bar";
      bar.setAttribute("aria-hidden", "true");
      label.className = "slt-prog-label";
      for (var i = 0; i < total; i++) {
        var seg = document.createElement("span");
        seg.className = "slt-prog-seg";
        seg.innerHTML = '<span class="slt-prog-fill"></span>';
        bar.appendChild(seg);
        segs.push(seg);
      }
      container.classList.add("slt-prog");
      container.appendChild(bar);
      container.appendChild(label);
      h = container._sltProg = {
        update: function (current, fn) {
          segs.forEach(function (sg, k) {
            if (k < current) sg.setAttribute("data-on", "");
            else sg.removeAttribute("data-on");
          });
          var text = typeof fn === "function" ? fn(current, total) : "";
          label.textContent = text || (window.SltFormat ? window.SltFormat.integer(current) + " / " + window.SltFormat.integer(total) : current + " / " + total);
        },
        destroy: function () {
          bar.remove();
          label.remove();
          container.classList.remove("slt-prog");
          delete container._sltProg;
        },
      };
    }
    h.update(o.current || 0, o.label);
    return h;
  }

  // Plays the 150 ms reverse. An overlay the owner only hid gets a copy shown
  // (ids removed, nothing duplicated); one the owner removed is put back.
  function ghost(node, isClone) {
    if (document.hidden || (isClone && !node.parentNode)) return;
    var g = node;
    if (isClone) {
      g = node.cloneNode(true);
      g.classList.remove("hidden");
      g.removeAttribute("hidden");
      g.removeAttribute("id");
      each(g.querySelectorAll("[id]"), function (n) {
        n.removeAttribute("id");
      });
    }
    ghosts.add(g);
    g.setAttribute("data-slt-ghost", "");
    g.setAttribute("aria-hidden", "true");
    g.inert = true;
    if (isClone) node.parentNode.insertBefore(g, node.nextSibling);
    else document.body.appendChild(g);
    setTimeout(function () {
      g.remove();
    }, 190);
  }

  // Follows the wizard's own DOM (verify.js builds a new .slt-v-content child
  // list for every step) and mirrors it: badge, progress, slide, shake, boxes.
  function wireWizard(h, dialog) {
    var content = dialog.querySelector(".slt-v-content");
    if (!content) return;
    var host = document.createElement("div");
    var lastStep = 0, lastErr = "", lastKey = "", queued = false;
    dialog.insertBefore(host, content);
    h.host = host;

    function label(c, total) {
      return tr("wizard.progress", { current: c, total: total });
    }
    function sync() {
      queued = false;
      var methods = content.querySelector(".slt-v-methods");
      var digits = content.querySelectorAll(".slt-v-digit");
      var step = digits.length ? 3 : methods ? 2 : content.querySelector('input[type="tel"]') ? 1 : 0;
      if (!step && content.querySelector(".slt-v-actions") && content.querySelector("h2")) step = 2; // no contact
      if (!step) return;
      each(digits, function (d) {
        if (d.value) d.setAttribute("data-filled", "");
        else d.removeAttribute("data-filled");
      });
      var err = content.querySelector(".slt-v-error:not(.hidden)");
      var text = err ? err.textContent.trim() : "";
      var icon = ["", "id-card", "message-square", "key-round"][step];
      var tone = "brand";
      var picked = content.querySelector(".slt-v-radio:checked");
      if (picked && picked.value === "email") icon = "mail";
      if (step === 3 && content.querySelector(".slt-v-timer-done")) {
        icon = "clock-alert";
        tone = "warning";
      } else if (step === 2 && !methods) {
        icon = "triangle-alert";
        tone = "warning";
      } else if (text) {
        icon = text === tr("verify.errors.network") ? "wifi-off" : "triangle-alert";
        tone = icon === "wifi-off" ? "warning" : "danger";
      }
      if (icon + tone !== lastKey) {
        lastKey = icon + tone;
        h.setBadge(icon, tone);
      }
      wizardProgress(host, { current: step, total: 3, label: label });
      if (lastStep && step !== lastStep) {
        var dir = step > lastStep ? "forward" : "back";
        transition(null, content, dir);
        emit("slt:wizard-step", { from: lastStep, to: step, direction: dir });
      } else if (text && text !== lastErr) {
        shake(content);
      }
      lastErr = text;
      lastStep = step;
    }
    function queue() {
      if (!queued) {
        queued = true;
        Promise.resolve().then(sync);
      }
    }
    var obs = new MutationObserver(function (list) {
      // The countdown rewrites its text every second: not worth a pass.
      var real = list.some(function (m) {
        var el = m.target.nodeType === 1 ? m.target : m.target.parentNode;
        return !(el && el.closest && el.closest(".slt-v-timer, .slt-v-sr"));
      });
      if (real) queue();
    });
    var events = ["input", "paste", "keyup", "change"];
    obs.observe(content, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "disabled"] });
    events.forEach(function (ev) {
      dialog.addEventListener(ev, queue);
    });
    h.cleanups.push(function () {
      obs.disconnect();
      events.forEach(function (ev) {
        dialog.removeEventListener(ev, queue);
      });
    });
    sync();
  }

  function enhance(overlay, opts) {
    opts = opts || {};
    if (!overlay || overlay.nodeType !== 1) return null;
    var old = handles.get(overlay);
    if (old) {
      if (opts.icon) old.setBadge(opts.icon, opts.tone);
      return old;
    }
    var card =
      overlay.querySelector(":scope > .modal-card, :scope > .slt-verify-card, :scope > [role='dialog']") ||
      overlay.firstElementChild;
    if (!card) return null;
    var wizard = card.classList.contains("slt-verify-card");
    overlay.setAttribute("data-slt-m", "");
    if (FLAT) overlay.setAttribute("data-slt-flat", "");

    var h = {
      badge: null,
      host: null,
      cleanups: [],
      setBadge: function (name, tone) {
        if (h.badge && root.icons) root.icons.setBadge(h.badge, name, tone);
      },
      detach: function () {
        h.cleanups.forEach(function (fn) {
          fn();
        });
        h.cleanups = [];
        handles.delete(overlay);
      },
      destroy: function () {
        h.detach();
        [h.badge, h.host].forEach(function (n) {
          if (n && n._sltProg) n._sltProg.destroy();
          if (n) n.remove();
        });
        overlay.removeAttribute("data-slt-m");
        overlay.removeAttribute("data-slt-flat");
      },
    };
    handles.set(overlay, h);

    if (typeof root.badge === "function") {
      h.badge = root.badge(opts.icon || "circle-check", { tone: opts.tone, label: opts.label });
      var anchor = wizard ? card.querySelector(".slt-v-content") : card.querySelector("h1, h2, h3");
      while (anchor && anchor.parentNode !== card) anchor = anchor.parentNode;
      card.insertBefore(h.badge, anchor || card.firstChild);
    }
    if (wizard) {
      wireWizard(h, card);
    } else {
      var open = isOpen(overlay);
      var obs = new MutationObserver(function () {
        var now = isOpen(overlay);
        if (now === open) return;
        open = now;
        if (!now) ghost(overlay, true);
        emit(now ? "slt:modal-open" : "slt:modal-close", overlay.id);
      });
      obs.observe(overlay, { attributes: true, attributeFilter: ["class", "hidden"] });
      h.cleanups.push(function () {
        obs.disconnect();
      });
    }
    return h;
  }

  function watchWizard() {
    if (bodyObs || !document.body) return;
    function isWizard(n) {
      return n.nodeType === 1 && n.classList.contains("slt-verify-overlay") && !ghosts.has(n);
    }
    function add(n) {
      if (!isWizard(n)) return;
      enhance(n, { icon: "id-card" });
      emit("slt:modal-open", "wizard");
    }
    each(document.querySelectorAll(".slt-verify-overlay"), add);
    bodyObs = new MutationObserver(function (list) {
      list.forEach(function (m) {
        each(m.addedNodes, add);
        each(m.removedNodes, function (n) {
          var h = n.nodeType === 1 && handles.get(n);
          if (!h) return;
          h.detach();
          ghost(n, false);
          emit("slt:modal-close", "wizard");
        });
      });
    });
    bodyObs.observe(document.body, { childList: true });
  }

  function init() {
    try {
      KNOWN.forEach(function (k) {
        var el = document.querySelector(k[0]);
        if (el) enhance(el, k[1]);
      });
      watchWizard();
    } catch (e) {
      /* never break the page */
    }
  }

  root.modal = {
    init: init,
    enhance: enhance,
    remove: function (o) {
      var h = handles.get(o);
      if (h) h.destroy();
    },
    setBadge: function (o, name, tone) {
      var h = handles.get(o);
      if (h) h.setBadge(name, tone);
    },
    wizardProgress: wizardProgress,
    transition: transition,
    shake: shake,
    success: success,
    watchWizard: watchWizard,
  };
})();
