// Existing user verification wizard: details, delivery choice, one time code.
//
//   SltVerify.open({ language, prefill: {name, phone}, backendBase,
//                    onVerified(user), onCancel() })
//   SltVerify.close()
//
// Server is authoritative. Every check done here (phone shape, six digits,
// the terms box) only decides whether a button is enabled. Codes and phone
// numbers are held in local variables for the life of the wizard only: they
// are never logged, never put in storage and never put in a URL.
// All text goes through SltI18n.t(key, vars); without i18n the key itself is
// shown. Endpoint contract: docs/IMPLEMENTATION_PLAN.md, "R3 backend".
(function () {
  "use strict";

  var CODE_LENGTH = 6;
  var TERMS_URL = "https://www.slt.lk/en/terms-conditions";
  var PRIVACY_URL = "https://www.slt.lk/en/privacy-policy";
  var ANNOUNCE_AT = [60, 30, 10, 0];
  var uid = 0;

  function t(key, vars) {
    var i18n = window.SltI18n;
    if (i18n && typeof i18n.t === "function") {
      try {
        var out = i18n.t(key, vars);
        if (typeof out === "string" && out) return out;
      } catch (err) {
        /* fall through to the key */
      }
    }
    return key;
  }

  function icon(name, size) {
    var icons = window.SltVerifyIcons;
    return icons ? icons.icon(name, size) : document.createTextNode("");
  }

  function now() {
    return window.performance && performance.now ? performance.now() : Date.now();
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v === true ? "" : String(v));
    });
    (children || []).forEach(function (c) {
      if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function defaultBase() {
    try {
      if (typeof window.backendHttpBase === "function") return window.backendHttpBase() || "";
    } catch (err) {
      /* use same origin */
    }
    return "";
  }

  // Usability check only, the server decides. Sri Lankan fixed line:
  // 0 + 2 digit area code + 7 digits, or the same behind +94 / 94 / 0094.
  function phoneLooksValid(raw) {
    var s = String(raw || "").replace(/[\s\-().]/g, "");
    return /^0[1-9]\d{8}$/.test(s) || /^(\+94|0094|94)[1-9]\d{8}$/.test(s);
  }

  function formatTime(seconds) {
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }

  var current = null; // the one open wizard, if any

  function open(options) {
    if (current) current.destroy(false);
    current = createWizard(options || {});
    return current;
  }

  function close() {
    if (current) current.destroy(false);
  }

  function createWizard(opts) {
    var base = typeof opts.backendBase === "string" ? opts.backendBase : defaultBase();
    base = base.replace(/\/+$/, "");
    var language = opts.language || document.documentElement.lang || "en";
    var prefill = opts.prefill || {};

    var form = { name: String(prefill.name || ""), phone: String(prefill.phone || ""), terms: false };
    var session = { ticket: null, methods: [], method: null, challengeId: null };
    var step = 0;
    var destroyed = false;
    var busy = false;
    var timers = [];
    var controller = null;
    var previousFocus = document.activeElement;
    var titleId = "slt-v-title-" + ++uid;

    // Shell: overlay > dialog (.card) > content
    var overlay = el("div", { class: "slt-verify-overlay" });
    var dialog = el("div", {
      class: "card slt-verify-card",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
      tabindex: "-1",
    });
    var content = el("div", { class: "slt-v-content" });
    dialog.appendChild(content);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    var prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    overlay.addEventListener("keydown", onKeydown);

    function clearTimers() {
      timers.forEach(function (id) {
        clearInterval(id);
      });
      timers = [];
    }

    function every(ms, fn) {
      timers.push(setInterval(fn, ms));
    }

    function destroy(cancelled) {
      if (destroyed) return;
      destroyed = true;
      clearTimers();
      if (controller) controller.abort();
      overlay.removeEventListener("keydown", onKeydown);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      document.body.style.overflow = prevOverflow;
      form.name = form.phone = "";
      session.ticket = session.challengeId = null;
      if (current === api) current = null;
      if (previousFocus && typeof previousFocus.focus === "function" && document.contains(previousFocus)) {
        try {
          previousFocus.focus();
        } catch (err) {
          /* ignore */
        }
      }
      if (cancelled && typeof opts.onCancel === "function") opts.onCancel();
    }

    // ---- network -------------------------------------------------------
    // Resolves {status, data} or {networkError: true}. Never rejects.
    function post(path, body) {
      if (controller) controller.abort();
      controller = typeof AbortController === "function" ? new AbortController() : null;
      var init = {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      };
      if (controller) init.signal = controller.signal;
      return fetch(base + path, init).then(
        function (res) {
          return res.json().then(
            function (data) {
              return { status: res.status, data: data || {}, headers: res.headers };
            },
            function () {
              return { status: res.status, data: {}, headers: res.headers };
            }
          );
        },
        function () {
          return { networkError: true };
        }
      );
    }

    function retryAfter(r) {
      var n = parseInt((r.data && r.data.retry_after) || (r.headers && r.headers.get("Retry-After")) || "0", 10);
      return n > 0 ? n : 0;
    }

    function rateLimitedText(r) {
      var wait = retryAfter(r);
      return t("verify.errors.rate_limited", { seconds: wait, count: wait });
    }

    // ---- shared pieces -------------------------------------------------
    function heading(key, vars) {
      return el("h2", { id: titleId, class: "slt-v-title", text: t(key, vars) });
    }

    function errorBox() {
      return el("p", { class: "slt-v-error hidden", role: "alert" });
    }

    function showError(box, message) {
      box.textContent = message || "";
      box.classList.toggle("hidden", !message);
    }

    function setBusy(buttons, on) {
      busy = on;
      dialog.setAttribute("aria-busy", on ? "true" : "false");
      buttons.forEach(function (b) {
        if (b) b.classList.toggle("slt-v-busy", on);
      });
    }

    function textButton(labelText, withBackIcon) {
      var b = el("button", { type: "button", class: "back-link slt-v-link" });
      if (withBackIcon) b.appendChild(icon("back", 16));
      b.appendChild(el("span", { text: labelText }));
      return b;
    }

    function mount(nodes, focusTarget) {
      clearTimers();
      content.textContent = "";
      nodes.forEach(function (n) {
        content.appendChild(n);
      });
      dialog.scrollTop = 0;
      overlay.scrollTop = 0;
      if (focusTarget) {
        try {
          focusTarget.focus();
        } catch (err) {
          /* ignore */
        }
      }
    }

    // ---- step 1: details ----------------------------------------------
    function renderStep1(message) {
      step = 1;
      var nameId = "slt-v-name-" + uid;
      var phoneId = "slt-v-phone-" + uid;
      var termsId = "slt-v-terms-" + uid;

      var nameInput = el("input", {
        id: nameId,
        class: "slt-v-input",
        type: "text",
        name: "name",
        autocomplete: "name",
        maxlength: "100",
        required: true,
        placeholder: t("verify.step1.name_placeholder"),
      });
      nameInput.value = form.name;
      var phoneInput = el("input", {
        id: phoneId,
        class: "slt-v-input",
        type: "tel",
        name: "phone",
        inputmode: "tel",
        autocomplete: "tel",
        maxlength: "20",
        required: true,
        placeholder: t("verify.step1.phone_placeholder"),
      });
      phoneInput.value = form.phone;
      var termsBox = el("input", { id: termsId, type: "checkbox", class: "slt-v-check" });
      termsBox.checked = form.terms;

      var termsLabel = el("label", { class: "slt-v-terms", for: termsId }, [
        el("span", { text: t("verify.step1.terms_prefix") + " " }),
        link(TERMS_URL, t("verify.step1.terms_link")),
        el("span", { text: " " + t("verify.step1.and") + " " }),
        link(PRIVACY_URL, t("verify.step1.privacy_link")),
      ]);

      var err = errorBox();
      var back = textButton(t("verify.step1.back"), true);
      var next = el("button", { type: "submit", class: "choice-button choice-button-solid slt-v-btn" }, [
        el("span", { text: t("verify.step1.next") }),
      ]);

      var formEl = el("form", { class: "slt-v-form", novalidate: true }, [
        el("div", { class: "slt-v-field" }, [
          el("label", { for: nameId, text: t("verify.step1.name_label") }),
          nameInput,
        ]),
        el("div", { class: "slt-v-field" }, [
          el("label", { for: phoneId, text: t("verify.step1.phone_label") }),
          phoneInput,
        ]),
        el("div", { class: "slt-v-field slt-v-field-check" }, [termsBox, termsLabel]),
        err,
        el("div", { class: "slt-v-actions" }, [next, back]),
      ]);

      function refresh() {
        form.name = nameInput.value;
        form.phone = phoneInput.value;
        form.terms = termsBox.checked;
        var ok = form.name.trim().length >= 2 && phoneLooksValid(form.phone) && form.terms;
        next.disabled = !ok || busy;
        next.setAttribute("aria-disabled", next.disabled ? "true" : "false");
      }
      [nameInput, phoneInput].forEach(function (i) {
        i.addEventListener("input", function () {
          showError(err, "");
          refresh();
        });
      });
      termsBox.addEventListener("change", function () {
        showError(err, "");
        refresh();
      });
      back.addEventListener("click", function () {
        destroy(true);
      });

      formEl.addEventListener("submit", function (e) {
        e.preventDefault();
        refresh();
        if (next.disabled || busy) return;
        showError(err, "");
        setBusy([next], true);
        next.disabled = true;
        post("/verify/lookup", {
          name: form.name.trim(),
          phone: form.phone.trim(),
          language: language,
        }).then(function (r) {
          if (destroyed) return;
          setBusy([next], false);
          refresh();
          if (r.networkError) return showError(err, t("verify.errors.network"));
          if (r.status === 200 && r.data.ticket) {
            session.ticket = r.data.ticket;
            session.methods = Array.isArray(r.data.methods) ? r.data.methods : [];
            session.method = null;
            return renderStep2();
          }
          if (r.status === 422) {
            var code = r.data && (r.data.code || (r.data.detail && r.data.detail.code));
            if (code === "phone_format") return showError(err, t("verify.errors.phone_format"));
            return showError(err, t("verify.errors.invalid_details"));
          }
          if (r.status === 429) return showError(err, rateLimitedText(r));
          showError(err, t("verify.errors.unavailable"));
        });
      });

      mount([heading("verify.step1.title"), formEl], nameInput);
      if (message) showError(err, message);
      refresh();
    }

    function link(href, text) {
      return el("a", { href: href, target: "_blank", rel: "noopener noreferrer", class: "slt-v-ext", text: text });
    }

    // ---- step 2: where to send ----------------------------------------
    function methodLabel(m) {
      var generic = m.generic && !m.masked; // hub mode: the mask is only known after sending
      var key = m.id === "email"
        ? (generic ? "verify.step2.email_generic" : "verify.step2.email_option")
        : (generic ? "verify.step2.sms_generic" : "verify.step2.sms_option");
      var text = t(key, { masked: m.masked });
      var parts = [el("span", { class: "slt-v-method-text", text: text })];
      if (m.masked && text.indexOf(m.masked) === -1) {
        parts.push(el("span", { class: "slt-v-masked", text: m.masked }));
      }
      return parts;
    }

    function renderStep2(message) {
      step = 2;
      var methods = session.methods.filter(function (m) {
        return m && (m.id === "sms" || m.id === "email");
      });
      var changeBtn = textButton(t("verify.step2.change_number"), true);
      changeBtn.addEventListener("click", function () {
        renderStep1();
      });

      if (!methods.length) {
        mount(
          [
            heading("verify.step2.title"),
            el("p", { class: "slt-v-error", role: "alert", text: t("verify.step2.no_contact") }),
            el("div", { class: "slt-v-actions" }, [changeBtn]),
          ],
          changeBtn
        );
        return;
      }

      var groupName = "slt-v-method-" + uid;
      var inputs = [];
      var rows = methods.map(function (m, idx) {
        var radio = el("input", {
          type: "radio",
          name: groupName,
          value: m.id,
          class: "slt-v-radio",
          id: groupName + "-" + m.id,
        });
        if (session.method ? session.method === m.id : idx === 0) radio.checked = true;
        inputs.push(radio);
        var row = el("label", { class: "slt-v-method", for: radio.id }, [
          radio,
          el("span", { class: "slt-v-method-icon" }, [icon(m.id === "email" ? "mail" : "sms", 22)]),
          el("span", { class: "slt-v-method-body" }, methodLabel(m)),
        ]);
        return row;
      });

      var err = errorBox();
      var send = el("button", { type: "submit", class: "choice-button choice-button-solid slt-v-btn" }, [
        icon("rocket", 20),
        el("span", { text: t("verify.step2.send_otp") }),
      ]);
      var group = el("div", { class: "slt-v-methods", role: "radiogroup", "aria-labelledby": titleId }, rows);
      var formEl = el("form", { class: "slt-v-form", novalidate: true }, [
        group,
        err,
        el("div", { class: "slt-v-actions" }, [send, changeBtn]),
      ]);

      function selected() {
        for (var i = 0; i < inputs.length; i++) if (inputs[i].checked) return inputs[i].value;
        return null;
      }

      formEl.addEventListener("submit", function (e) {
        e.preventDefault();
        if (busy) return;
        var method = selected();
        if (!method) return;
        session.method = method;
        showError(err, "");
        setBusy([send], true);
        send.disabled = true;
        sendCode(function (r) {
          if (destroyed) return;
          setBusy([send], false);
          send.disabled = false;
          if (r.ok) return renderStep3(r.data);
          showError(err, r.message);
          if (r.noContact) {
            session.methods = session.methods.filter(function (m) {
              return m.id !== method;
            });
            renderStep2(r.message);
          } else if (r.ticketGone) {
            renderStep1(r.message);
          }
        });
      });

      mount([heading("verify.step2.title"), formEl], selected() ? inputs.filter(function (i) { return i.checked; })[0] : inputs[0]);
      if (message) showError(err, message);
    }

    // Sends (or resends) a code. cb gets {ok, data} or {ok:false, message,
    // noContact, ticketGone, retry}.
    function sendCode(cb) {
      post("/verify/otp/send", { ticket: session.ticket, method: session.method, language: language }).then(function (r) {
        if (r.networkError) return cb({ ok: false, message: t("verify.errors.network") });
        if (r.status === 200 && r.data.challenge_id) {
          session.challengeId = r.data.challenge_id;
          return cb({ ok: true, data: r.data });
        }
        var code = r.data && r.data.code;
        if (r.status === 409 || code === "no_contact") {
          return cb({ ok: false, noContact: true, message: t("verify.step2.no_contact") });
        }
        if (r.status === 429) {
          var wait = retryAfter(r);
          if (code === "cooldown") {
            return cb({ ok: false, retry: wait, message: t("verify.errors.cooldown", { seconds: wait, count: wait }) });
          }
          return cb({ ok: false, retry: wait, message: rateLimitedText(r) });
        }
        if (r.status === 401 || r.status === 403 || r.status === 410 || r.status === 422) {
          return cb({ ok: false, ticketGone: true, message: t("verify.errors.generic") });
        }
        cb({ ok: false, message: t("verify.errors.unavailable") });
      });
    }

    // ---- step 3: enter the code ---------------------------------------
    function renderStep3(sent) {
      step = 3;
      var expiresIn = Math.max(1, parseInt(sent.expires_in, 10) || 120);
      var resendIn = Math.max(0, parseInt(sent.resend_in, 10) || 30);
      var endAt = now() + expiresIn * 1000;
      var resendAt = now() + resendIn * 1000;
      var state = "active"; // active | expired | locked
      var lastShown = -1;
      var announced = {};
      var digits = [];

      var timerText = el("span", { class: "slt-v-timer-value" });
      var timer = el("p", { class: "slt-v-timer", "aria-hidden": "true" }, [timerText]);
      var live = el("p", { class: "slt-v-sr", "aria-live": "polite", "aria-atomic": "true" });
      var err = errorBox();
      var row = el("div", { class: "slt-v-digits", role: "group", "aria-labelledby": titleId });

      for (var i = 0; i < CODE_LENGTH; i++) {
        var d = el("input", {
          type: "text",
          class: "slt-v-digit",
          inputmode: "numeric",
          pattern: "[0-9]*",
          autocomplete: "one-time-code",
          maxlength: "1",
          autocapitalize: "off",
          autocorrect: "off",
          spellcheck: "false",
          "aria-label": t("verify.step3.digit_aria", { n: i + 1, index: i + 1, total: CODE_LENGTH }),
        });
        digits.push(d);
        row.appendChild(d);
      }

      var verifyBtn = el("button", { type: "submit", class: "choice-button choice-button-solid slt-v-btn" }, [
        icon("check", 20),
        el("span", { text: t("verify.step3.verify") }),
      ]);
      var resendBtn = el("button", { type: "button", class: "choice-button choice-button-outline slt-v-btn" }, [
        el("span", { class: "slt-v-resend-label", text: t("verify.step3.resend") }),
      ]);
      var cooldownNote = el("p", { class: "slt-v-hint hidden" });
      var backBtn = textButton(t("verify.step1.back"), true);

      var masked = sent.masked || "";
      var subtitleKey = masked ? "verify.step3.subtitle" : "verify.step3.subtitle_generic";
      var subtitle = el("p", { class: "slt-v-sub", text: t(subtitleKey, { masked: masked }) });

      var formEl = el("form", { class: "slt-v-form", novalidate: true }, [
        subtitle,
        row,
        timer,
        live,
        err,
        el("div", { class: "slt-v-actions" }, [verifyBtn, resendBtn, cooldownNote, backBtn]),
      ]);

      function code() {
        return digits
          .map(function (x) {
            return x.value;
          })
          .join("");
      }

      function refreshVerify() {
        var ready = state === "active" && /^\d{6}$/.test(code());
        verifyBtn.disabled = !ready || busy;
      }

      function fillFrom(start, text) {
        var only = String(text || "").replace(/\D/g, "");
        if (!only) return;
        var k = 0;
        for (var p = start; p < CODE_LENGTH && k < only.length; p++, k++) digits[p].value = only.charAt(k);
        var focusAt = Math.min(start + only.length, CODE_LENGTH - 1);
        digits[focusAt].focus();
        digits[focusAt].select();
        showError(err, "");
        refreshVerify();
      }

      digits.forEach(function (inp, idx) {
        inp.addEventListener("input", function () {
          var raw = inp.value.replace(/\D/g, "");
          if (raw.length > 1) {
            inp.value = "";
            fillFrom(idx, raw);
            return;
          }
          inp.value = raw;
          showError(err, "");
          if (raw && idx < CODE_LENGTH - 1) digits[idx + 1].focus();
          refreshVerify();
        });
        inp.addEventListener("keydown", function (e) {
          if (e.key === "Backspace" && !inp.value && idx > 0) {
            e.preventDefault();
            digits[idx - 1].value = "";
            digits[idx - 1].focus();
            refreshVerify();
          } else if (e.key === "ArrowLeft" && idx > 0) {
            e.preventDefault();
            digits[idx - 1].focus();
          } else if (e.key === "ArrowRight" && idx < CODE_LENGTH - 1) {
            e.preventDefault();
            digits[idx + 1].focus();
          }
        });
        inp.addEventListener("paste", function (e) {
          var text = (e.clipboardData || window.clipboardData).getData("text");
          e.preventDefault();
          fillFrom(0, text);
        });
        inp.addEventListener("focus", function () {
          inp.select();
        });
      });

      function setDigitsDisabled(off) {
        digits.forEach(function (x) {
          x.disabled = off;
        });
      }

      function clearDigits() {
        digits.forEach(function (x) {
          x.value = "";
        });
      }

      function announce(text) {
        live.textContent = "";
        // A fresh node text on the next frame makes repeated messages speak.
        setTimeout(function () {
          if (!destroyed) live.textContent = text;
        }, 30);
      }

      function setState(next, message) {
        state = next;
        var off = next !== "active";
        setDigitsDisabled(off);
        resendBtn.classList.toggle("choice-button-solid", off);
        resendBtn.classList.toggle("choice-button-outline", !off);
        timer.classList.toggle("slt-v-timer-done", next === "expired");
        refreshVerify();
        if (message) showError(err, message);
        if (off) updateResend();
      }

      function updateResend() {
        var left = Math.max(0, Math.ceil((resendAt - now()) / 1000));
        resendBtn.disabled = left > 0 || busy;
        cooldownNote.classList.toggle("hidden", left <= 0);
        if (left > 0) cooldownNote.textContent = t("verify.errors.cooldown", { seconds: left, count: left });
      }

      function tick() {
        var left = Math.max(0, Math.ceil((endAt - now()) / 1000));
        if (left !== lastShown && state !== "locked") {
          lastShown = left;
          timerText.textContent = t("verify.step3.countdown", { time: formatTime(left), seconds: left });
          ANNOUNCE_AT.forEach(function (mark) {
            if (left <= mark && !announced[mark] && mark <= expiresIn) {
              announced[mark] = true;
              if (mark === 0) announce(t("verify.errors.expired"));
              else announce(t("verify.step3.countdown", { time: formatTime(left), seconds: left }));
            }
          });
        }
        if (left <= 0 && state === "active") {
          setState("expired", t("verify.step3.expired"));
        }
        updateResend();
      }

      verifyBtn.disabled = true;

      formEl.addEventListener("submit", function (e) {
        e.preventDefault();
        if (busy || state !== "active") return;
        var value = code();
        if (!/^\d{6}$/.test(value)) return;
        showError(err, "");
        setBusy([verifyBtn], true);
        verifyBtn.disabled = true;
        post("/verify/otp/verify", { challenge_id: session.challengeId, code: value }).then(function (r) {
          if (destroyed) return;
          setBusy([verifyBtn], false);
          if (r.networkError) {
            refreshVerify();
            return showError(err, t("verify.errors.network"));
          }
          if (r.status === 200 && r.data) {
            var user = { display_name: r.data.display_name, language: r.data.language || language };
            destroy(false);
            if (typeof opts.onVerified === "function") opts.onVerified(user);
            return;
          }
          var c = r.data && r.data.code;
          if (r.status === 401 || c === "invalid") {
            clearDigits();
            digits[0].focus();
            refreshVerify();
            var left = r.data && typeof r.data.attempts_left === "number" ? r.data.attempts_left : null;
            var msg = t("verify.errors.invalid");
            if (left !== null) msg += " " + t("verify.errors.attempts_left", { count: left, attempts: left });
            return showError(err, msg);
          }
          if (r.status === 410 || c === "expired") {
            endAt = now();
            return tick();
          }
          if (r.status === 423 || c === "locked") {
            clearDigits();
            return setState("locked", t("verify.errors.locked"));
          }
          if (r.status === 429) {
            refreshVerify();
            return showError(err, rateLimitedText(r));
          }
          refreshVerify();
          showError(err, t("verify.errors.unavailable"));
        });
      });

      resendBtn.addEventListener("click", function () {
        if (busy || resendBtn.disabled) return;
        showError(err, "");
        setBusy([resendBtn], true);
        resendBtn.disabled = true;
        sendCode(function (r) {
          if (destroyed) return;
          setBusy([resendBtn], false);
          if (r.ok) {
            clearTimers();
            return renderStep3(r.data);
          }
          if (r.retry) resendAt = now() + r.retry * 1000;
          updateResend();
          if (r.ticketGone) return renderStep1(r.message);
          showError(err, r.message);
        });
      });

      backBtn.addEventListener("click", function () {
        renderStep2();
      });

      mount([heading("verify.step3.title"), formEl], digits[0]);
      tick();
      every(250, tick);
    }

    // ---- keyboard: Escape is Back, Tab is trapped ----------------------
    function onKeydown(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        if (step === 3) renderStep2();
        else if (step === 2) renderStep1();
        else destroy(true);
        return;
      }
      if (e.key !== "Tab") return;
      var nodes = Array.prototype.filter.call(
        dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'),
        function (n) {
          return !n.disabled && n.offsetParent !== null;
        }
      );
      if (!nodes.length) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      var first = nodes[0];
      var last = nodes[nodes.length - 1];
      var active = document.activeElement;
      if (e.shiftKey && (active === first || !dialog.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !dialog.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    }

    // Focus that escapes the dialog (for example a click on the backdrop) is
    // pulled back in.
    overlay.addEventListener("mousedown", function (e) {
      if (e.target === overlay) e.preventDefault();
    });

    var api = { destroy: destroy };
    // Load the right dictionary first (a no-op if the app already did).
    var i18n = window.SltI18n;
    if (i18n && typeof i18n.setLanguage === "function") {
      Promise.resolve()
        .then(function () {
          return i18n.setLanguage(language);
        })
        .catch(function () {})
        .then(function () {
          if (!destroyed) renderStep1();
        });
    } else {
      renderStep1();
    }
    return api;
  }

  // What the server says about the visitor's session: {verified, expired,
  // hubSession}. The server decides; this only reads GET /verify/session.
  function sessionState(backendBase) {
    var base = typeof backendBase === "string" ? backendBase : defaultBase();
    return fetch(base.replace(/\/+$/, "") + "/verify/session", {
      credentials: "include",
      headers: { Accept: "application/json" },
    }).then(
      function (res) {
        return res.json().then(
          function (data) {
            data = data || {};
            return { verified: data.verified === true, expired: data.expired === true, hubSession: data.hub_session === true };
          },
          function () {
            return { verified: false, expired: false, hubSession: false };
          }
        );
      },
      function () {
        return { verified: false, expired: false, hubSession: false };
      }
    );
  }

  window.SltVerify = { open: open, close: close, sessionState: sessionState, _phoneLooksValid: phoneLooksValid };
  window.SLTVerify = window.SltVerify;
})();
