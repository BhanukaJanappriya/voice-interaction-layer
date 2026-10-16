/* Singlish to Sinhala typing for the chat box (frontend/singlish.js).
 *
 * A plain script, no build step. In the browser it exposes window.SltSinglish;
 * in Node it is loadable with require() for the tests (module.exports guard).
 *
 *   SltSinglish.convert(latin)   Latin text to Sinhala (pure, idempotent)
 *   SltSinglish.enable(input)    turn live typing on (input defaults to #chat-input)
 *   SltSinglish.disable()        turn it off (the choice is remembered)
 *   SltSinglish.isEnabled()      the toggle state
 *   SltSinglish.attach(input) / detach()   bind or unbind the listeners
 *   SltSinglish.deleteBefore(text, caret)  the grapheme aware backspace
 *
 * Typing scheme (Helakuru like, original mapping table):
 *   vowels      a aa(A) ae aae i ii u uu e ee o oo ai au, Ru (ඍ) Ruu (ඎ)
 *   consonants  k kh g gh c/ch chh j jh jn(ඤ) gn(ඥ) t/T(ට) th(ත) Th(ථ)
 *               d(ද) dh(ධ) D(ඩ) Dh(ඪ) n N(ණ) p ph b bh m y r l L(ළ) w/v
 *               s sh/S(ශ) Sh(ෂ) h f
 *   prenasalized  mb ඹ, ndh ඳ, nD ඬ, nG ඟ  (plain nd, ng stay clusters)
 *   anusvara    x, and n before k or g inside a word (lanka = ලංකා, sangeetha = සංගීත)
 *   visarga     H
 *   hal         a consonant with no vowel gets the al-lakuna when a consonant follows
 *               or the word ends (kamak = කමක්, kamaka = කමක)
 *   ZWJ         r and y right after a consonant make rakaransaya / yansaya (kr ක්‍ර, ky ක්‍ය)
 *   Latin letters with no mapping (q, X ...), digits, punctuation, URLs and e-mail
 *   addresses are left alone.
 */
(function (root) {
  "use strict";

  var HAL = "්";
  var ZWJ = "‍";
  var ANUSVARA = "ං";
  var VISARGA = "ඃ";

  // independent vowel, vowel sign (sign "" means the inherent a)
  var VOWELS = {
    "aae": ["ඈ", "ෑ"],
    "aa": ["ආ", "ා"],
    "ae": ["ඇ", "ැ"],
    "ai": ["ඓ", "ෛ"],
    "au": ["ඖ", "ෞ"],
    "a": ["අ", ""],
    "ii": ["ඊ", "ී"],
    "i": ["ඉ", "ි"],
    "uu": ["ඌ", "ූ"],
    "u": ["උ", "ු"],
    "ee": ["ඒ", "ේ"],
    "e": ["එ", "ෙ"],
    "oo": ["ඕ", "ෝ"],
    "o": ["ඔ", "ො"],
    "Ruu": ["ඎ", "ෲ"],
    "Ru": ["ඍ", "ෘ"],
    "A": ["ආ", "ා"]
  };

  var CONSONANTS = {
    "chh": "ඡ", "ndh": "ඳ",
    "kh": "ඛ", "gh": "ඝ", "ch": "ච", "jh": "ඣ", "jn": "ඤ", "gn": "ඥ",
    "th": "ත", "Th": "ථ", "dh": "ධ", "Dh": "ඪ", "ph": "ඵ", "bh": "භ",
    "sh": "ශ", "Sh": "ෂ", "mb": "ඹ", "nD": "ඬ", "nG": "ඟ",
    "k": "ක", "g": "ග", "c": "ච", "j": "ජ", "t": "ට", "T": "ට",
    "d": "ද", "D": "ඩ", "n": "න", "N": "ණ", "p": "ප", "b": "බ",
    "m": "ම", "y": "ය", "r": "ර", "l": "ල", "L": "ළ", "w": "ව",
    "v": "ව", "s": "ස", "S": "ශ", "h": "හ", "f": "ෆ", "z": "ස"
  };
  // n before these letters becomes the anusvara (lanka, sangeetha)
  var ANUSVARA_BEFORE = { "nk": "ක", "nkh": "ඛ", "ng": "ග", "ngh": "ඝ" };

  var VOWEL_KEYS = ["aae", "Ruu", "aa", "ae", "ai", "au", "ii", "uu", "ee", "oo", "Ru", "a", "i", "u", "e", "o", "A"];
  var CONS_KEYS = Object.keys(CONSONANTS).sort(function (a, b) { return b.length - a.length; });
  var ANU_KEYS = ["nkh", "ngh", "nk", "ng"];

  function isLetter(ch) {
    return (ch >= "a" && ch <= "z") || (ch >= "A" && ch <= "Z");
  }
  function isSinhalaConsonant(ch) {
    var c = ch.charCodeAt(0);
    return c >= 0x0D9A && c <= 0x0DC6;
  }
  function isIndependentVowel(ch) {
    var c = ch.charCodeAt(0);
    return c >= 0x0D85 && c <= 0x0D96;
  }
  function isMark(ch) {
    var c = ch.charCodeAt(0);
    return c === 0x0D82 || c === 0x0D83 || (c >= 0x0DCA && c <= 0x0DDF) || c === 0x0DF2 || c === 0x0DF3 || c === 0x200D;
  }

  function matchAt(run, i, keys) {
    for (var k = 0; k < keys.length; k++) {
      if (run.substr(i, keys[k].length) === keys[k]) return keys[k];
    }
    return null;
  }

  // Converts one run of ASCII letters. prevChar is the character before the run
  // (used to decide whether an anusvara has something to attach to).
  function convertRun(run, prevChar) {
    var out = "";
    var i = 0;
    var prevCons = "";      // key of the consonant just written without a vowel
    while (i < run.length) {
      var v = matchAt(run, i, VOWEL_KEYS);
      var lastCh = out ? out.charAt(out.length - 1) : (prevChar || "");
      var afterHal = out.length > 0 && lastCh === HAL;
      if (v) {
        out += VOWELS[v][0];
        i += v.length;
        prevCons = "";
        continue;
      }
      // anusvara + consonant (n before k or g), only after a vowel or a consonant with a vowel
      var anu = matchAt(run, i, ANU_KEYS);
      var startsWord = !out && !(prevChar && isSinhalaConsonantOrMark(prevChar));
      if (anu && !afterHal && !startsWord && !(lastCh === ZWJ)) {
        out += ANUSVARA;
        var base = ANUSVARA_BEFORE[anu];
        i += anu.length;
        out += base;
        i = finishConsonant(run, i, function (s) { out += s; });
        prevCons = "";
        // finishConsonant reports through the callback; the hal case is handled there
        if (out.charAt(out.length - 1) === HAL) prevCons = anu;
        continue;
      }
      if (run.charAt(i) === "x" || run.charAt(i) === "H") {
        out += run.charAt(i) === "x" ? ANUSVARA : VISARGA;
        i += 1;
        prevCons = "";
        continue;
      }
      var key = matchAt(run, i, CONS_KEYS);
      if (!key && run.charAt(i) !== run.charAt(i).toLowerCase()) {
        // an unmapped capital (Kamal, Oya) is typed as its lower case letter
        run = run.slice(0, i) + run.charAt(i).toLowerCase() + run.slice(i + 1);
        continue;
      }
      if (!key) {
        // no mapping: keep the letter as typed
        out += run.charAt(i);
        i += 1;
        prevCons = "";
        continue;
      }
      var cons = CONSONANTS[key];
      i += key.length;
      // rakaransaya / yansaya
      if (afterHal && (key === "r" || key === "y") && prevCons && prevCons !== key) {
        out += ZWJ + cons;
      } else {
        out += cons;
      }
      i = finishConsonant(run, i, function (s) { out += s; });
      prevCons = out.charAt(out.length - 1) === HAL ? key : "";
    }
    return out;
  }

  function isSinhalaConsonantOrMark(ch) {
    return isSinhalaConsonant(ch) || isMark(ch) || isIndependentVowel(ch);
  }

  // After a consonant: a following vowel becomes its sign, anything else gives a hal.
  function finishConsonant(run, i, emit) {
    var v = matchAt(run, i, VOWEL_KEYS);
    if (v) {
      emit(VOWELS[v][1]);
      return i + v.length;
    }
    emit(HAL);
    return i;
  }

  function protectedChunk(chunk) {
    return chunk.indexOf("@") >= 0 || chunk.indexOf("://") >= 0 || /^www\./i.test(chunk);
  }

  function convert(text) {
    if (text === null || text === undefined) return "";
    text = String(text);
    var chunks = text.split(/(\s+)/);
    var out = "";
    for (var c = 0; c < chunks.length; c++) {
      var chunk = chunks[c];
      if (!chunk || /^\s+$/.test(chunk) || protectedChunk(chunk)) { out += chunk; continue; }
      var i = 0;
      var piece = "";
      while (i < chunk.length) {
        var ch = chunk.charAt(i);
        if (isLetter(ch)) {
          var j = i;
          while (j < chunk.length && isLetter(chunk.charAt(j))) j++;
          var prev = piece ? piece.charAt(piece.length - 1) : "";
          piece += convertRun(chunk.slice(i, j), prev);
          i = j;
        } else {
          piece += ch;
          i += 1;
        }
      }
      out += piece;
    }
    return out.normalize ? out.normalize("NFC") : out;
  }

  // Backspace / Delete over a whole cluster. Returns {text, caret}.
  function deleteBefore(text, caret) {
    if (caret <= 0) return { text: text, caret: caret };
    var j = caret;
    while (j > 0 && isMark(text.charAt(j - 1))) j--;
    var start;
    if (j < caret && j > 0 && (isSinhalaConsonant(text.charAt(j - 1)) || isIndependentVowel(text.charAt(j - 1)))) {
      start = j - 1;
    } else if (j < caret) {
      start = caret - 1;                    // stray mark: remove just the last one
    } else {
      start = caret - 1;
      var code = text.charCodeAt(start);
      if (code >= 0xDC00 && code <= 0xDFFF && start > 0) start -= 1;   // surrogate pair
      if (isSinhalaConsonant(text.charAt(start)) || isIndependentVowel(text.charAt(start))) start = j - 1;
    }
    var rest = text.slice(0, start);
    // drop a dangling hal + ZWJ left from a rakaransaya / yansaya
    if (rest.charAt(rest.length - 1) === ZWJ) rest = rest.slice(0, -1);
    return { text: rest + text.slice(caret), caret: rest.length };
  }

  function deleteAfter(text, caret) {
    if (caret >= text.length) return { text: text, caret: caret };
    var end = caret + 1;
    var first = text.charAt(caret);
    if (isSinhalaConsonant(first) || isIndependentVowel(first)) {
      while (end < text.length && isMark(text.charAt(end))) end++;
    }
    return { text: text.slice(0, caret) + text.slice(end), caret: caret };
  }

  // ---------------------------------------------------------------- browser part

  var STORE_KEY = "slt.singlish";
  var state = {
    input: null,
    pref: null,          // true / false once the user chose, null = follow the language
    last: "",
    pending: null,       // {raw, out, start, end}
    composing: false,
    before: null,
    busy: false,
    button: null
  };

  function readPref() {
    try {
      var v = root.localStorage.getItem(STORE_KEY);
      if (v === "on") return true;
      if (v === "off") return false;
    } catch (err) { /* storage blocked */ }
    return null;
  }
  function writePref(on) {
    state.pref = on;
    try { root.localStorage.setItem(STORE_KEY, on ? "on" : "off"); } catch (err) { /* ignore */ }
  }
  function language() {
    try {
      if (root.SltI18n && typeof root.SltI18n.language === "function") return root.SltI18n.language();
    } catch (err) { /* ignore */ }
    return null;
  }
  function languageAllows() {
    var lang = language();
    return lang === null || lang === "si";
  }
  function preference() {
    if (state.pref !== null) return state.pref;
    var stored = readPref();
    if (stored !== null) return stored;
    return language() === "si";
  }
  function active() {
    return !!state.input && languageAllows() && preference();
  }

  function setValue(input, value, caret) {
    state.busy = true;
    try {
      input.value = value;
      if (typeof input.setSelectionRange === "function") input.setSelectionRange(caret, caret);
    } finally {
      state.busy = false;
    }
    state.last = value;
  }

  function onInput(event) {
    var input = state.input;
    if (!input || state.busy) return;
    var value = input.value;
    var last = state.last;
    var b = state.before;
    state.before = null;
    state.last = value;
    if (state.composing || (event && event.isComposing)) { state.pending = null; return; }
    if (!active()) { state.pending = null; return; }
    var caret = typeof input.selectionStart === "number" ? input.selectionStart : value.length;
    if (value.length <= last.length && !(event && /^insert/.test(event.inputType || "") )) { state.pending = null; return; }
    var start;
    var inserted;
    var len = b ? value.length - (b.value.length - (b.end - b.start)) : 0;
    if (b && len > 0 && b.start + len <= value.length && value.slice(0, b.start) === b.value.slice(0, b.start)) {
      start = b.start;                       // exact: the selection before the edit and the grown length
      inserted = value.slice(start, start + len);
      caret = start + len;
    } else {
      var prefix = 0;                        // fallback: compare with the previous value
      var max = Math.min(last.length, value.length, caret);
      while (prefix < max && last.charAt(prefix) === value.charAt(prefix)) prefix++;
      start = Math.min(prefix, caret);
      inserted = value.slice(start, caret);
    }
    if (!inserted) { state.pending = null; return; }

    if (inserted.length === 1) {
      var ch = inserted;
      if (!isLetter(ch)) { state.pending = null; return; }
      var p = state.pending;
      var raw = ch;
      var from = start;
      if (p && p.end === start && value.slice(p.start, p.end) === p.out && p.raw.length < 40) {
        raw = p.raw + ch;
        from = p.start;
      }
      var prevChar = from > 0 ? value.charAt(from - 1) : "";
      var out = convertRun(raw, prevChar);
      out = out.normalize ? out.normalize("NFC") : out;
      var next = value.slice(0, from) + out + value.slice(caret);
      var newCaret = from + out.length;
      setValue(input, next, newCaret);
      state.pending = { raw: raw, out: out, start: from, end: newCaret };
      return;
    }
    // paste or multi character insert: convert only the inserted text
    var conv = convert(inserted);
    var combined = value.slice(0, start) + conv + value.slice(caret);
    setValue(input, combined, start + conv.length);
    state.pending = null;
  }

  function onBeforeInput(event) {
    var input = state.input;
    if (!input || !active() || state.composing) return;
    var type = event.inputType;
    state.before = { value: input.value, start: input.selectionStart, end: input.selectionEnd };
    if (type !== "deleteContentBackward" && type !== "deleteContentForward") return;
    if (input.selectionStart !== input.selectionEnd) return;
    var res = type === "deleteContentBackward"
      ? deleteBefore(input.value, input.selectionStart)
      : deleteAfter(input.value, input.selectionStart);
    event.preventDefault();
    state.pending = null;
    setValue(input, res.text, res.caret);
    try {
      state.busy = true;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (err) { /* ignore */ } finally { state.busy = false; }
  }

  function resetPending() { state.pending = null; state.last = state.input ? state.input.value : ""; }
  function onCompositionStart() { state.composing = true; state.pending = null; }
  function onCompositionEnd() { state.composing = false; state.pending = null; state.last = state.input ? state.input.value : ""; }

  var HANDLERS = [
    ["input", onInput],
    ["beforeinput", onBeforeInput],
    ["compositionstart", onCompositionStart],
    ["compositionend", onCompositionEnd],
    ["blur", resetPending],
    ["focus", resetPending]
  ];

  function attach(input) {
    if (!input) return false;
    if (state.input === input) return true;
    detach();
    state.input = input;
    state.last = input.value || "";
    state.pending = null;
    for (var i = 0; i < HANDLERS.length; i++) input.addEventListener(HANDLERS[i][0], HANDLERS[i][1]);
    return true;
  }

  function detach() {
    var input = state.input;
    if (!input) return;
    for (var i = 0; i < HANDLERS.length; i++) input.removeEventListener(HANDLERS[i][0], HANDLERS[i][1]);
    state.input = null;
    state.pending = null;
  }

  function findInput() {
    return root.document ? root.document.getElementById("chat-input") : null;
  }

  function enable(input) {
    attach(input || state.input || findInput());
    writePref(true);
    refreshButton();
  }
  function disable() {
    writePref(false);
    state.pending = null;
    refreshButton();
  }
  function isEnabled() { return preference(); }

  // ---- optional toggle button (#singlish-toggle, see docs/patches/C_singlish.md)
  function label(key, fallback) {
    try {
      if (root.SltI18n && typeof root.SltI18n.t === "function") {
        var t = root.SltI18n.t(key);
        if (t && t !== key) return t;
      }
    } catch (err) { /* ignore */ }
    return fallback;
  }
  function refreshButton() {
    var b = state.button;
    if (!b) return;
    var on = preference();
    b.hidden = !languageAllows();
    b.setAttribute("aria-pressed", on ? "true" : "false");
    var text = on ? label("chat.singlish.on", "Singlish typing is on. Press to turn it off")
                  : label("chat.singlish.off", "Singlish typing is off. Press to turn it on");
    b.setAttribute("aria-label", text);
    b.setAttribute("title", text);
    b.classList.toggle("is-on", on);
  }
  function toggle() {
    if (preference()) disable(); else enable();
    if (state.input && typeof state.input.focus === "function") state.input.focus();
  }

  function init() {
    var input = findInput();
    if (input) attach(input);
    state.pref = readPref();
    var b = root.document.getElementById("singlish-toggle");
    if (b) {
      state.button = b;
      b.addEventListener("click", function (e) { e.preventDefault(); toggle(); });
    }
    root.document.addEventListener("slt:language", refreshButton);
    refreshButton();
  }

  var api = {
    convert: convert,
    enable: enable,
    disable: disable,
    isEnabled: isEnabled,
    attach: attach,
    detach: detach,
    deleteBefore: deleteBefore,
    deleteAfter: deleteAfter,
    toggle: toggle
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root && root.document) {
    root.SltSinglish = api;
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})(typeof window !== "undefined" ? window : this);
