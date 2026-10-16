"""Static scan for hardcoded user facing strings (requirement R4).

The scan is deliberately simple and deterministic:

* Frontend JS: every string literal that looks like English prose (two or more
  words, or a single capitalised word of 4+ letters in a UI sink) outside a
  comment, console call, throw, selector or URL context.
* Frontend HTML: visible text nodes and the attributes placeholder, title,
  aria-label and alt of frontend/index.html.
* Backend Python (AST based): string constants of 3+ words that are not
  docstrings, not logging, not inside raise (except HTTPException detail), and
  not SQL. Sinhala or Tamil literals outside backend/i18n and frontend/i18n are
  also findings: translations belong in the json files.

Known findings live in tests/i18n_allowlist.txt, one per line:

    <relative path> | <normalised string> | <reason>

Only NEW strings fail. A string that was fixed (moved to a key) should be
removed from the allowlist; a stale allowlist line also fails, so the list
cannot rot. Run `python tests/test_i18n_hardcoded.py --regen` to print the
current findings for the audit (it never rewrites the allowlist).
"""

import ast
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = Path(__file__).resolve().parent / "i18n_allowlist.txt"

JS_FILES_EXTRA = ["frontend/app.js", "frontend/livekit-voice.js", "frontend/bot3d.js",
                  "frontend/avatar/loader.js", "frontend/avatar/avatar.js", "frontend/i18n/i18n.js"]
JS_GLOBS = ["frontend/ui/**/*.js", "frontend/verify/*.js"]
JS_SKIP = {"frontend/verify/icons.js", "frontend/ui/icons/icons.js", "frontend/ui/ui.js"}
HTML_FILES = ["frontend/index.html"]
# Backend modules that never put text in front of the customer: translation
# files, database code, the MCP hub internals, developer diagnostics, voice
# engine plumbing, the admin portal (English by decision) and the Live and
# router internals (prompts and failure classes). Everything else is scanned,
# so a new backend module is checked by default.
PY_INTERNAL = (
    "i18n/", "db/", "mcp_server/", "mcp_client/", "mcp_gateway/", "knowledge/", "voicekit/", "gemini_live/",
    "voice_router/reasons.py", "voice_router/types.py", "voice_router/router.py", "llm_chain/errors.py",
    "llm_chain/tiers.py", "livekit_inference.py", "url_safety.py", "vad.py", "tts.py", "stt.py", "admin/",
    "log_safety.py", "config.py", "verification/seed_demo.py", "verification/service.py", "auth/tokens.py",
    "auth/manage_users.py", "feedback/models.py",
)
PY_SKIP_NAMES = {"_selftest.py", "__main__.py", "seed.py", "seed_demo.py", "migrate.py", "__init__.py"}
KEY_LIMIT = 100  # findings are compared by their first 100 characters

WILDCARD = "*non-latin"  # allowlist text that covers every Sinhala or Tamil literal in a file
NON_LATIN_KIND = "translation table outside the json files"
NON_LATIN = re.compile(r"[඀-෿஀-௿]")
WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
CODEISH = re.compile(r"[#.\[\]<>=/\\_|:;(){}$*^~@]|https?:|\.(js|css|json|png|svg|jpg|mjs|glb|wasm)\b")
SQLISH = re.compile(r"\b(IN \(|SELECT|INSERT|UPDATE|DELETE|CREATE TABLE|ALTER TABLE|FROM|WHERE|PRAGMA)\b")
JS_SINK = re.compile(r"textContent|innerText|setCaption|addBubble|aria-label|\btitle\b|placeholder|\blabel\b|alert\(|showVerifyError|setStatus|\btext:")
JS_SKIP_LINE = re.compile(
    r"console\.|throw new|require\(|import |querySelector|classList|getAttribute|addEventListener|"
    r"new URL|getElementById|localStorage|sessionStorage|removeAttribute|createElement|\.matches\(|"
    r"dispatchEvent|CustomEvent|\.closest\(|setProperty|\.style\.|Error\(|fetch\(|\.test\(|\.match\(|"
    r"\.replace\(|\.split\(|\.includes\(|\.indexOf\(|postMessage|JSON\.|\.setAttribute\(\s*[\"'](class|id|role|type|href|"
    r"src|data-|aria-(hidden|modal|live|pressed|expanded|haspopup|controls|describedby|labelledby|"
    r"busy|current|atomic|checked|valuemin|valuemax|valuenow))")


def norm(text):
    return re.sub(r"\s+", " ", text.replace("\n", " ").replace("\u2014", "-").replace("\u2013", "-"))[:KEY_LIMIT].strip()


# JS ---------------------------------------------------------------------

def _js_literals(source):
    """Yields (line, literal) for every ", ' and ` string; skips comments."""
    i, n, line = 0, len(source), 1
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "\n":
            line += 1
            i += 1
        elif c == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                i += 1
        elif c == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            end = n if end < 0 else end + 2
            line += source.count("\n", i, end)
            i = end
        elif c in "\"'`":
            quote, start, start_line = c, i + 1, line
            i += 1
            buf = []
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    buf.append(source[i + 1])
                    i += 2
                    continue
                if source[i] == "\n":
                    line += 1
                    if quote != "`":
                        break
                buf.append(source[i])
                i += 1
            i += 1
            yield start_line, "".join(buf)
        elif c == "/" and (not source[:i].rstrip() or source[:i].rstrip()[-1] in "=(,:;!&|?{}[\n"):
            # regex literal: skip to the closing slash
            i += 1
            while i < n and source[i] not in "/\n":
                i += 2 if source[i] == "\\" else 1
            i += 1
        else:
            i += 1


def _looks_like_prose(text):
    """Two or more words, starts with a letter or digit, no css or identifier tokens."""
    if not re.match(r"[A-Za-z0-9]", text) or "  " in text:
        return False
    if " " not in text or text in ("use strict", "Bearer X", "noopener noreferrer") or re.match(r"[a-z]+ [a-z-]*X?$", text) or re.search(r"[a-z]-[a-z]|[a-z]_[a-z]|[a-z]\.[a-z]|\w\(|[#\[\]{}<>=/\|$^~@]|https?:|\d(px|ms|s|em|rem|%)\b", text):
        return False
    words = WORD.findall(text)
    return len(words) >= 2 and any(len(w) >= 3 for w in words) and bool(re.search(r"[a-z]{2}", text))


def scan_js(path, root=ROOT):
    rel = path.relative_to(root).as_posix()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found = []
    for line_no, literal in _js_literals("\n".join(lines)):
        text = norm(re.sub(r"\$\{[^}]*\}", "X", literal))
        if len(text) < 3:
            continue
        src_line = lines[line_no - 1] if line_no - 1 < len(lines) else ""
        if NON_LATIN.search(text):
            found.append((rel, line_no, text, NON_LATIN_KIND))
            continue
        if JS_SKIP_LINE.search(src_line) or "<" in text and ">" in text and not re.search(r">[^<]*[A-Za-z]{4,} [A-Za-z]{3,}", text):
            continue
        if _looks_like_prose(text) or (JS_SINK.search(src_line) and re.fullmatch(r"[A-Z][a-z]{3,}[.…:!?]*", text)):
            found.append((rel, line_no, text, "english literal"))
    return found


# HTML -------------------------------------------------------------------

class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.skip = 0
        self.in_comment = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.skip += 1
        attrs = dict(attrs)
        has_key = any(k.startswith("data-i18n") for k in attrs)
        for name in ("placeholder", "title", "aria-label", "alt"):
            value = attrs.get(name)
            if value and WORD.search(value) and not has_key:
                self.items.append((self.getpos()[0], f"{name}={norm(value)}"))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self.skip:
            return
        text = norm(data)
        if text and re.search(r"[A-Za-z]{2}", text) and not NON_LATIN.search(text):
            self.items.append((self.getpos()[0], f"text={text}"))


def scan_html(path, root=ROOT):
    rel = path.relative_to(root).as_posix()
    parser = _Html()
    parser.feed(path.read_text(encoding="utf-8"))
    # Text under a data-i18n element is handled by data-i18n: drop is not
    # possible with this simple parser, so such elements are simply absent
    # until the patch lands; after it lands their allowlist lines go stale.
    return [(rel, line, text, "html text or attribute") for line, text in parser.items]


# Python -----------------------------------------------------------------

_LOG_NAMES = {"logger", "log", "logging", "print", "_log", "LOG"}


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value
        return (base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")), f.attr
    if isinstance(f, ast.Name):
        return "", f.id
    return "", ""


class _PyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.items = []
        self.docstrings = set()
        self.skip_depth = 0

    def _mark_doc(self, node):
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
            self.docstrings.add(id(body[0].value))

    def visit_Module(self, node):
        self._mark_doc(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._mark_doc(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._mark_doc(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        base, name = _call_name(node)
        quiet = (base in _LOG_NAMES or name in _LOG_NAMES or name in {"debug", "info", "warning", "error", "exception", "critical"}
                 or name in {"getenv", "get", "add_argument", "ArgumentParser", "Field", "getLogger", "Path", "open",
                             "execute", "text", "Query", "Header", "Depends", "APIRouter", "FastAPI", "mount"})
        if quiet:
            self.skip_depth += 1
            self.generic_visit(node)
            self.skip_depth -= 1
        else:
            self.generic_visit(node)

    def visit_Raise(self, node):
        exc = node.exc
        keep = isinstance(exc, ast.Call) and _call_name(exc)[1] == "HTTPException"
        if keep:
            self.generic_visit(node)
        # other exceptions are developer facing: skipped

    def visit_Assert(self, node):
        return

    def visit_Dict(self, node):
        # dictionary keys are identifiers, only values can be prose
        for value in node.values:
            if value is not None:
                self.visit(value)

    def visit_Subscript(self, node):
        self.visit(node.value)

    def visit_Compare(self, node):
        return  # comparisons with literals are logic, not output

    def visit_Constant(self, node):
        if self.skip_depth or not isinstance(node.value, str) or id(node) in self.docstrings:
            return
        self.items.append((node.lineno, node.value))


def scan_py(path, root=ROOT):
    rel = path.relative_to(root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    visitor = _PyVisitor()
    visitor.visit(tree)
    found = []
    for line, value in visitor.items:
        text = norm(value)
        if NON_LATIN.search(text):
            found.append((rel, line, text, NON_LATIN_KIND))
            continue
        words = WORD.findall(text)
        if " " in text and len(words) >= 3 and re.search(r"[a-z]{3}", text) and not SQLISH.search(text) and not CODEISH.search(
                re.sub(r"[.,:;!?'\"()’—–…{}-]", " ", text)):
            found.append((rel, line, text, "english literal"))
    return found


# Collection -------------------------------------------------------------

def js_files():
    files = [ROOT / p for p in JS_FILES_EXTRA]
    for pattern in JS_GLOBS:
        files += sorted(ROOT.glob(pattern))
    return [f for f in files if f.exists() and f.relative_to(ROOT).as_posix() not in JS_SKIP]


def py_files():
    out = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        rel = path.relative_to(ROOT / "backend").as_posix()
        if "__pycache__" in rel or path.name in PY_SKIP_NAMES or rel.startswith(PY_INTERNAL):
            continue
        out.append(path)
    return out


def all_findings():
    found = []
    for path in js_files():
        found += scan_js(path)
    for name in HTML_FILES:
        found += scan_html(ROOT / name)
    for path in py_files():
        found += scan_py(path)
    return found


def _is_allowed(allowed, path, text, kind):
    return (path, text) in allowed or (kind == NON_LATIN_KIND and (path, WILDCARD) in allowed)


def load_allowlist():
    entries = {}
    if not ALLOWLIST.exists():
        return entries
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = [p.strip() for p in raw.split(" | ", 2)]
        assert len(parts) == 3 and parts[2], f"allowlist line needs path | string | reason: {raw[:80]}"
        entries[(parts[0], parts[1])] = parts[2]
    return entries


# Tests ------------------------------------------------------------------

def test_allowlist_lines_have_reasons():
    load_allowlist()


def test_no_new_hardcoded_user_facing_strings():
    allowed = load_allowlist()
    new = sorted({(p, t) for p, _l, t, k in all_findings() if not _is_allowed(allowed, p, t, k)})
    assert not new, (
        "New hardcoded user facing strings. Add a key to frontend/i18n or backend/i18n (en, si, ta) and use it, "
        "or add the line to tests/i18n_allowlist.txt with a reason:\n"
        + "\n".join(f"  {p} | {t}" for p, t in new[:60])
        + (f"\n  ... and {len(new) - 60} more" if len(new) > 60 else "")
    )


def test_allowlist_has_no_stale_lines():
    found = all_findings()
    seen = {(p, t) for p, _l, t, _k in found}
    wild = {p for p, _l, _t, k in found if k == NON_LATIN_KIND}
    stale = sorted(k for k in load_allowlist() if k not in seen and not (k[1] == WILDCARD and k[0] in wild))
    assert not stale, "Allowlist lines that no longer match anything (remove them, the string was fixed):\n" + "\n".join(
        f"  {p} | {t}" for p, t in stale[:60])


def test_scanner_detects_planted_strings(tmp_path):
    js = tmp_path / "x.js"
    js.write_text("""el.textContent = "Please try again later";
console.log("debug message here");
// "comment only words"
const T = {si: "සුබ"};
const U = {si: "සුබ"};
""", encoding="utf-8")
    found = scan_js(js, tmp_path)
    assert [t for _p, _l, t, k in found if k == "english literal"] == ["Please try again later"]
    assert any(k == NON_LATIN_KIND for _p, _l, _t, k in found)
    html = tmp_path / "x.html"
    html.write_text('<title>My page title</title><button title="Close it" data-i18n="a.b">Keyed text</button>'
                    '<input placeholder="Type here now"><script>var a = "not html text";</script>', encoding="utf-8")
    texts = [t for _p, _l, t, _k in scan_html(html, tmp_path)]
    assert "text=My page title" in texts and "placeholder=Type here now" in texts
    assert not any("not html text" in t for t in texts)
    assert not any(t.startswith("title=Close") for t in texts)  # keyed elements are fine
    py = tmp_path / "x.py"
    py.write_text("""\"\"\"Module docstring with many words.\"\"\"
import logging
logger = logging.getLogger(__name__)


def f():
    logger.info("this is a log line here")
    return "Sorry, something went wrong here"
""".replace('\\"', '"'), encoding="utf-8")
    assert [t for _p, _l, t, _k in scan_py(py, tmp_path)] == ["Sorry, something went wrong here"]
    assert _looks_like_prose("Please try again later")
    assert not _looks_like_prose("#screen-voice .card")
    assert not _looks_like_prose("application/json")


if __name__ == "__main__":
    if "--regen" in sys.argv:
        for path, line, text, kind in all_findings():
            print(f"{path}:{line} | {kind} | {text}")
