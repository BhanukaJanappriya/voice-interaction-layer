/* Small translation helper shared by the verification wizard and any other
 * UI that needs Sinhala, Tamil or English strings.
 *
 *   await SltI18n.setLanguage("si");
 *   SltI18n.t("verify.step3.countdown", {time: "01:45"});
 *   SltI18n.language();
 *
 * Strings live in en.json, si.json and ta.json next to this script. A missing
 * key or a failed load falls back to English, then to the key itself, so the
 * UI never shows an empty label. Elements with data-i18n get their text
 * replaced, data-i18n-aria sets aria-label, data-i18n-placeholder sets the
 * placeholder. */
(function () {
  "use strict";

  var SUPPORTED = ["en", "si", "ta"];
  var scriptUrl = (document.currentScript && document.currentScript.src) || "";
  var dictionaries = {};
  var loading = {};
  var current = "en";

  function normalise(lang) {
    var short = String(lang || "en").toLowerCase().slice(0, 2);
    return SUPPORTED.indexOf(short) >= 0 ? short : "en";
  }

  function load(lang) {
    if (dictionaries[lang]) return Promise.resolve(dictionaries[lang]);
    if (loading[lang]) return loading[lang];
    var url = new URL(lang + ".json", scriptUrl || document.baseURI).href;
    loading[lang] = fetch(url, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        dictionaries[lang] = data;
        return data;
      })
      .catch(function () {
        dictionaries[lang] = {};
        return dictionaries[lang];
      })
      .then(function (data) {
        delete loading[lang];
        return data;
      });
    return loading[lang];
  }

  function format(template, vars) {
    return String(template).replace(/\{(\w+)\}/g, function (whole, name) {
      return vars && vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : whole;
    });
  }

  function lookup(key) {
    var own = dictionaries[current];
    if (own && typeof own[key] === "string" && own[key] !== "") return own[key];
    var base = dictionaries.en;
    if (base && typeof base[key] === "string" && base[key] !== "") return base[key];
    return key;
  }

  function t(key, vars) {
    return format(lookup(key), vars);
  }

  function apply(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria")));
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
    });
    scope.querySelectorAll("[data-i18n-alt]").forEach(function (el) {
      el.setAttribute("alt", t(el.getAttribute("data-i18n-alt")));
    });
  }

  function setLanguage(lang) {
    var wanted = normalise(lang);
    var jobs = [load("en")];
    if (wanted !== "en") jobs.push(load(wanted));
    return Promise.all(jobs).then(function () {
      current = wanted;
      document.documentElement.lang = wanted;
      apply(document);
      var title = t("app.title");
      if (title && title !== "app.title") document.title = title;
      try {
        document.dispatchEvent(new CustomEvent("slt:language", { detail: { language: wanted } }));
      } catch (err) { /* old browser: dynamic labels repaint on their next change */ }
      return wanted;
    });
  }

  window.SltI18n = {
    setLanguage: setLanguage,
    t: t,
    language: function () { return current; },
    apply: apply,
  };
})();
