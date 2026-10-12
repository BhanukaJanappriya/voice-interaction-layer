/* Bootstrap for the optional UI layer (docs/UI_PLAN.md section 8).
 *
 * Loaded after the ui modules and before app.js. Everything here is decoration:
 * each step runs inside its own try and catch, and the page works the same if
 * any of it, or this whole file, is missing. The "ui-on" class is added to the
 * root only after the start finished without an error, and the new CSS is
 * scoped to it or to new class names.
 *
 * It reads existing signals only (the bot's state class, the wizard's
 * aria-busy attribute); it does not change any existing element's behavior.
 */
(function () {
  "use strict";
  var U = (window.SltUi = window.SltUi || {});

  function t(key, fallback) {
    try {
      if (window.SltI18n) {
        var v = window.SltI18n.t(key);
        if (v && v !== key) return v;
      }
    } catch (e) { /* use the fallback */ }
    return fallback;
  }

  function safe(fn) {
    try { fn(); return true; } catch (e) { return false; }
  }

  // The logo loader follows two signals that already exist: the wizard dialog's
  // aria-busy (set by verify.js while it sends or checks a code) and the voice
  // avatar's state class (connecting shows as calibrating, a reply as thinking).
  function bindLoader() {
    if (!U.loader || !U.loader.show) return;

    var wizardHandle = null;
    function wizardBusy(dialog) {
      var busy = dialog.getAttribute("aria-busy") === "true";
      if (busy && !wizardHandle) {
        var verifying = !!dialog.querySelector(".slt-v-digit");
        wizardHandle = U.loader.show(dialog, {
          size: "sm",
          label: verifying ? t("loader.verifying", "Verifying...") : t("loader.sending", "Sending your code..."),
        });
      } else if (!busy && wizardHandle) {
        wizardHandle.hide();
        wizardHandle = null;
      }
    }
    new MutationObserver(function (records) {
      records.forEach(function (r) {
        if (r.target && r.target.getAttribute && r.target.getAttribute("role") === "dialog") wizardBusy(r.target);
      });
    }).observe(document.body, { attributes: true, attributeFilter: ["aria-busy"], subtree: true });

    var bot = document.getElementById("bot-avatar");
    var stage = document.querySelector(".voice-stage");
    var botHandle = null;
    if (bot && stage) {
      new MutationObserver(function () {
        var cls = bot.className;
        var waiting = /\bstate-(calibrating|thinking)\b/.test(cls);
        if (waiting && !botHandle) {
          botHandle = U.loader.show(stage, {
            size: "sm",
            label: /\bstate-calibrating\b/.test(cls) ? t("loader.connecting", "Connecting...") : t("loader.thinking", "Thinking..."),
          });
        } else if (!waiting && botHandle) {
          botHandle.hide();
          botHandle = null;
        }
      }).observe(bot, { attributes: true, attributeFilter: ["class"] });
    }
  }

  var ok = true;
  if (U.icons && U.icons.init) ok = safe(function () { U.icons.init(); }) && ok;
  if (U.modal && U.modal.init) ok = safe(function () { U.modal.init(); }) && ok;
  ok = safe(bindLoader) && ok;
  if (ok) document.documentElement.classList.add("ui-on");
  U.ready = ok;
})();
