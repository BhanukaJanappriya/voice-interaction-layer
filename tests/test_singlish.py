"""frontend/singlish.js: Singlish (Latin) to Sinhala typing.

The module is a plain script that also loads in Node, so these tests run it
there (skipped when Node is not installed). The live typing tests drive the
real event handlers with a small fake input element.

Typing choices (see the header of frontend/singlish.js):
  ayubowan   typed aayuboowan  (long aa and oo are explicit)
  fibre      typed fayibar     (ෆයිබර්)
  package    typed paekeej     (පැකේජ්)
  wadak      typed waeDak      (වැඩක්, capital D is the retroflex ඩ)
  sri lanka  typed shrii lankaa (ශ්‍රී ලංකා, n before k is the anusvara)
"""

import json
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SINGLISH_JS = ROOT / "frontend" / "singlish.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run(script):
    code = "const S = require(%s);\nconsole.log(JSON.stringify((function(){ %s })()));" % (json.dumps(str(SINGLISH_JS)), script)
    done = subprocess.run([NODE, "-e", code], capture_output=True, timeout=60, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout)


def convert_all(words):
    return run("return %s.map(w => S.convert(w));" % json.dumps(words))


def check(cases):
    got = convert_all([c for c, _ in cases])
    for (typed, expected), actual in zip(cases, got):
        assert actual == expected, (typed, actual, expected)


WORDS = [
    ("aayuboowan", "ආයුබෝවන්"),
    ("shrii lankaa", "ශ්‍රී ලංකා"),
    ("mama", "මම"),
    ("oyaa", "ඔයා"),
    ("kohomada", "කොහොමද"),
    ("hari", "හරි"),
    ("bohoma sthuthi", "බොහොම ස්තුති"),
    ("pasuwa", "පසුව"),
    ("ganna", "ගන්න"),
    ("denna", "දෙන්න"),
    ("waeDak", "වැඩක්"),
    ("fayibar", "ෆයිබර්"),
    ("paekeej", "පැකේජ්"),
    ("bila", "බිල"),
    ("mudala", "මුදල"),
]


def test_common_words():
    check(WORDS)


def test_digraphs():
    check([
        ("tha", "ත"), ("Tha", "ථ"), ("dha", "ධ"), ("Dha", "ඪ"), ("cha", "ච"), ("chha", "ඡ"),
        ("sha", "ශ"), ("Sha", "ෂ"), ("kha", "ඛ"), ("gha", "ඝ"), ("pha", "ඵ"), ("bha", "භ"),
        ("ta", "ට"), ("Na", "ණ"), ("La", "ළ"), ("Da", "ඩ"), ("jna", "ඤ"), ("gna", "ඥ"),
        ("mba", "ඹ"), ("ndha", "ඳ"), ("nDa", "ඬ"), ("nGa", "ඟ"), ("fa", "ෆ"),
    ])


def test_vowels_and_signs():
    check([
        ("ka", "ක"), ("kaa", "කා"), ("kae", "කැ"), ("kaae", "කෑ"), ("ki", "කි"), ("kii", "කී"),
        ("ku", "කු"), ("kuu", "කූ"), ("ke", "කෙ"), ("kee", "කේ"), ("ko", "කො"), ("koo", "කෝ"),
        ("kai", "කෛ"), ("kau", "කෞ"), ("kRu", "කෘ"), ("kRuu", "කෲ"),
        ("a", "අ"), ("aa", "ආ"), ("ae", "ඇ"), ("aae", "ඈ"), ("i", "ඉ"), ("ii", "ඊ"), ("u", "උ"),
        ("uu", "ඌ"), ("e", "එ"), ("ee", "ඒ"), ("o", "ඔ"), ("oo", "ඕ"), ("ai", "ඓ"), ("au", "ඖ"),
        ("Ru", "ඍ"), ("Ruu", "ඎ"), ("ru", "රු"),
    ])


def test_hal_rules():
    check([
        ("kamak", "කමක්"),     # word final consonant gets the al-lakuna
        ("kamaka", "කමක"),     # a vowel after it removes the hal
        ("kal", "කල්"),
        ("sthuthi", "ස්තුති"),   # consonant before consonant
        ("ganna", "ගන්න"),
        ("tatta", "ටට්ට"),
        ("balla", "බල්ල"),
        ("midda", "මිද්ද"),
        ("k", "ක්"),
    ])


def test_zwj_rakaransaya_and_yansaya():
    out = convert_all(["krama", "kya", "prasna", "shrii", "mayya"])
    assert out[0] == "ක්‍රම"
    assert out[1] == "ක්‍ය"
    assert out[2] == "ප්‍රස්න"
    assert out[3] == "ශ්‍රී"
    assert out[4] == "මය්ය"          # y after y is a plain hal, no ZWJ
    for text in out[:4]:
        assert "‍" in text


def test_anusvara_and_visarga():
    check([("lankaa", "ලංකා"), ("sangeetha", "සංගේත"), ("kax", "කං"), ("kaH", "කඃ"), ("ganga", "ගංග")])


def test_leaves_other_text_alone():
    out = convert_all(["0771234567", "a@b.com", "https://slt.lk/x", "www.slt.lk", "Hello, 5G!", ""])
    assert out[0] == "0771234567"
    assert out[1] == "a@b.com"
    assert out[2] == "https://slt.lk/x"
    assert out[3] == "www.slt.lk"
    assert "," in out[4] and "5" in out[4] and "!" in out[4]
    assert out[5] == ""


def test_capitals_fall_back_to_lowercase():
    check([("Kamal", "කමල්")])


def test_output_is_valid_sinhala_and_nfc():
    typed = [w for w, _ in WORDS] + ["krama", "kya", "sangeetha", "pasuwa", "lankaa kohomada"]
    for text in convert_all(typed):
        assert unicodedata.normalize("NFC", text) == text
        for ch in text:
            code = ord(ch)
            assert ch == " " or 0x0D80 <= code <= 0x0DFF or code == 0x200D, (text, hex(code))
        assert text.count("‍") == text.count("්‍")   # ZWJ only after a hal
        assert "�" not in text


def test_idempotent_on_sinhala_text():
    texts = [expected for _, expected in WORDS] + ["ශ්‍රී ලංකා", "කොහොමද? 123"]
    again = run("return %s.map(t => S.convert(t));" % json.dumps(texts))
    assert again == texts
    twice = run("return %s.map(w => S.convert(S.convert(w)));" % json.dumps([w for w, _ in WORDS]))
    assert twice == [e for _, e in WORDS]


def test_mixed_sinhala_and_latin_keeps_the_sinhala():
    check([("කොහොමද mama", "කොහොමද මම")])


BACKSPACE_CASES = [
    ("කොහොමද", "කොහොම"),         # consonant with no sign
    ("කොහො", "කො"),             # consonant plus its vowel sign
    ("කමක්", "කම"),              # hal goes with its consonant
    ("ක්‍ර", "ක්"),              # rakaransaya: drops ra and the ZWJ, keeps ka plus hal
    ("ශ්‍රී", "ශ්"),             # ra plus its vowel sign together
    ("ලංකා", "ලං"),              # long a sign with ka
    ("අ", ""),
    ("ab", "a"),
    ("", ""),
]


def test_backspace_deletes_a_whole_cluster():
    for text, expected in BACKSPACE_CASES:
        got = run("return S.deleteBefore(%s, %d);" % (json.dumps(text), len(text)))
        assert got["text"] == expected, (text, got)
        assert got["caret"] == len(expected)


def test_backspace_in_the_middle_keeps_the_caret():
    text = "කොහොමද"
    got = run("return S.deleteBefore(%s, 4);" % json.dumps(text))   # caret after කො හො
    assert got["text"] == "කොමද"
    assert got["caret"] == 2
    assert run("return S.deleteBefore('abc', 0);")["text"] == "abc"


def test_delete_forward_removes_a_cluster():
    got = run("return S.deleteAfter(%s, 0);" % json.dumps("කොහොමද"))
    assert got["text"] == "හොමද"


FAKE = r"""
const store = {};
global.window = {
  document: { readyState: "complete", getElementById: () => null, addEventListener() {} },
  localStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
  SltI18n: { language: () => global.__lang || "si", t: k => k },
};
global.Event = class { constructor(type, o) { this.type = type; Object.assign(this, o || {}); } };
const S = require(__PATH__);
const api = window.SltSinglish;

function makeInput() {
  const el = {
    value: "", selectionStart: 0, selectionEnd: 0, handlers: {},
    addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
    removeEventListener(t, f) { this.handlers[t] = (this.handlers[t] || []).filter(x => x !== f); },
    setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
    dispatchEvent(ev) { (this.handlers[ev.type] || []).forEach(f => f(ev)); return true; },
    fire(type, ev) {
      ev.type = type; ev.defaultPrevented = false;
      ev.preventDefault = function () { this.defaultPrevented = true; };
      (this.handlers[type] || []).forEach(f => f(ev));
      return ev;
    },
    type(ch) {
      this.fire("beforeinput", { inputType: "insertText", data: ch });
      const c = this.selectionStart;
      this.value = this.value.slice(0, c) + ch + this.value.slice(this.selectionEnd);
      this.selectionStart = this.selectionEnd = c + ch.length;
      this.fire("input", { inputType: "insertText", data: ch });
    },
    backspace() {
      const ev = this.fire("beforeinput", { inputType: "deleteContentBackward" });
      if (ev.defaultPrevented) return;
      const c = this.selectionStart;
      if (c > 0) { this.value = this.value.slice(0, c - 1) + this.value.slice(c); this.selectionStart = this.selectionEnd = c - 1; }
      this.fire("input", { inputType: "deleteContentBackward" });
    },
    paste(text) {
      this.fire("beforeinput", { inputType: "insertFromPaste" });
      const c = this.selectionStart;
      this.value = this.value.slice(0, c) + text + this.value.slice(this.selectionEnd);
      this.selectionStart = this.selectionEnd = c + text.length;
      this.fire("input", { inputType: "insertFromPaste" });
    },
  };
  return el;
}
function typeAll(el, text) { for (const ch of text) el.type(ch); }
function reset(el) { el.value = ""; el.selectionStart = el.selectionEnd = 0; }
"""


def live(script):
    code = FAKE.replace("__PATH__", json.dumps(str(SINGLISH_JS)))
    code += "\nconsole.log(JSON.stringify((function(){ %s })()));" % script
    done = subprocess.run([NODE, "-e", code], capture_output=True, timeout=60, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout)


def test_live_typing_converts_as_you_type():
    out = live("""
      const el = makeInput(); api.enable(el);
      typeAll(el, "kamak"); const a = el.value;
      el.type("a"); const b = el.value;
      typeAll(el, " kohomada"); const c = el.value;
      return {a, b, c, caret: el.selectionStart, len: el.value.length};
    """)
    assert out["a"] == "කමක්"
    assert out["b"] == "කමක"
    assert out["c"] == "කමක කොහොමද"
    assert out["caret"] == out["len"]


def test_live_typing_in_the_middle_keeps_the_caret():
    out = live("""
      const el = makeInput(); api.enable(el);
      typeAll(el, "mama oyaa");
      el.selectionStart = el.selectionEnd = 1;
      typeAll(el, "k");
      return {value: el.value, caret: el.selectionStart};
    """)
    assert out["value"] == "මක්ම ඔයා"
    assert out["caret"] == 3


def test_live_backspace_removes_whole_clusters():
    out = live("""
      const el = makeInput(); api.enable(el);
      typeAll(el, "kohomada"); el.backspace(); const a = el.value;
      el.backspace(); const b = el.value;
      return {a, b, caret: el.selectionStart};
    """)
    assert out["a"] == "කොහොම"
    assert out["b"] == "කොහො"
    assert out["caret"] == 4


def test_live_paste_converts_latin_and_keeps_sinhala():
    out = live("""
      const el = makeInput(); api.enable(el);
      el.paste("kohomada ආයුබෝවන්");
      return el.value;
    """)
    assert out == "කොහොමද ආයුබෝවන්"


def test_live_off_and_other_languages_do_not_convert():
    out = live("""
      const el = makeInput(); api.enable(el); api.disable();
      typeAll(el, "mama"); const off = el.value; const flag = api.isEnabled();
      api.enable(el);
      global.__lang = "en"; reset(el); typeAll(el, "mama"); const en = el.value;
      global.__lang = "ta"; reset(el); typeAll(el, "mama"); const ta = el.value;
      global.__lang = "si"; reset(el); typeAll(el, "mama"); const si = el.value;
      return {off, flag, en, ta, si, stored: store["slt.singlish"]};
    """)
    assert out["off"] == "mama"
    assert out["flag"] is False
    assert out["en"] == "mama"
    assert out["ta"] == "mama"
    assert out["si"] == "මම"
    assert out["stored"] == "on"


def test_live_composition_is_not_converted():
    out = live("""
      const el = makeInput(); api.enable(el);
      el.fire("compositionstart", {});
      el.value = "ab"; el.selectionStart = el.selectionEnd = 2;
      el.fire("input", { inputType: "insertCompositionText", isComposing: true });
      el.fire("compositionend", {});
      return el.value;
    """)
    assert out == "ab"


def test_default_follows_the_language_until_the_user_chooses():
    out = live("""
      const el = makeInput(); api.attach(el);
      global.__lang = "si"; const si = api.isEnabled();
      global.__lang = "en"; const en = api.isEnabled();
      return {si, en};
    """)
    assert out["si"] is True
    assert out["en"] is False
