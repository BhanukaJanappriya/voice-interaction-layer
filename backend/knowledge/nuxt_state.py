"""Reads the window.__NUXT__ state that www.sltmobitel.lk embeds in every
server rendered page.

The site is a Nuxt 2 app. Its server HTML carries almost no visible body
text (package tables and most page bodies are fetched later in the
browser), but it does carry the app's store: navigation (every page URL),
the footer, home page tiles in English, Sinhala and Tamil, promotional
banners and SEO descriptions. Nuxt serializes that store as a minified
JavaScript expression, not JSON:

    window.__NUXT__=(function(a,b,c){return {layout:"default",x:a,y:[b,c]}}(null,1,"text"));

Repeated values become function parameters and keys are unquoted, so
json.loads cannot read it. This module parses that small subset of
JavaScript (object and array literals, strings, numbers, identifiers,
true/false/null, void 0, !0 and !1) with a tiny recursive descent parser,
then substitutes the arguments for the parameters. Pure Python on
purpose: evaluating the page's own script with a JavaScript engine would
mean running code downloaded from the internet, and would add a Node.js
dependency to the build.
"""

import re
from typing import Any, Optional

_MARKER = "window.__NUXT__="


class NuxtParseError(ValueError):
    pass


class _Ref:
    """A reference to a function parameter, resolved after the arguments
    (which come last in the source) have been parsed."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name


_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


class _Parser:
    def __init__(self, src: str):
        self.src = src
        self.pos = 0

    # Low level helpers

    def _skip_ws(self) -> None:
        src, n = self.src, len(self.src)
        while self.pos < n and src[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek(self) -> str:
        self._skip_ws()
        return self.src[self.pos] if self.pos < len(self.src) else ""

    def _expect(self, token: str) -> None:
        self._skip_ws()
        if not self.src.startswith(token, self.pos):
            raise NuxtParseError(f"expected {token!r} at {self.pos}: {self.src[self.pos:self.pos + 40]!r}")
        self.pos += len(token)

    def _ident(self) -> str:
        self._skip_ws()
        match = _IDENT.match(self.src, self.pos)
        if not match:
            raise NuxtParseError(f"expected identifier at {self.pos}: {self.src[self.pos:self.pos + 40]!r}")
        self.pos = match.end()
        return match.group(0)

    def _string(self) -> str:
        quote = self.src[self.pos]
        self.pos += 1
        out = []
        src = self.src
        while True:
            if self.pos >= len(src):
                raise NuxtParseError("unterminated string")
            ch = src[self.pos]
            if ch == quote:
                self.pos += 1
                break
            if ch == "\\":
                nxt = src[self.pos + 1]
                if nxt == "u":
                    if src[self.pos + 2] == "{":
                        end = src.index("}", self.pos)
                        out.append(chr(int(src[self.pos + 3:end], 16)))
                        self.pos = end + 1
                    else:
                        out.append(chr(int(src[self.pos + 2:self.pos + 6], 16)))
                        self.pos += 6
                    continue
                if nxt == "x":
                    out.append(chr(int(src[self.pos + 2:self.pos + 4], 16)))
                    self.pos += 4
                    continue
                if nxt in "\r\n":  # line continuation
                    self.pos += 2
                    continue
                out.append(_SIMPLE_ESCAPES.get(nxt, nxt))
                self.pos += 2
                continue
            out.append(ch)
            self.pos += 1
        text = "".join(out)
        # 😀 style surrogate pairs arrive as two separate code
        # units; recombine them into one character.
        try:
            return text.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            return text

    # Grammar

    def value(self) -> Any:
        ch = self._peek()
        if ch == "{":
            return self._object()
        if ch == "[":
            return self._array()
        if ch in "\"'":
            return self._string()
        if ch == "!":
            self.pos += 1
            operand = self.value()
            return not operand
        if ch == "-" or ch == "." or ch.isdigit():
            match = _NUMBER.match(self.src, self.pos)
            if not match:
                raise NuxtParseError(f"bad number at {self.pos}")
            self.pos = match.end()
            text = match.group(0)
            number = float(text)
            return int(number) if number.is_integer() and "." not in text and "e" not in text.lower() else number
        name = self._ident()
        if name == "true":
            return True
        if name == "false":
            return False
        if name in ("null", "undefined"):
            return None
        if name == "void":
            self.value()
            return None
        if name == "Array" and self._peek() == "(":
            # Array(n): n empty slots.
            self._expect("(")
            size = self.value()
            self._expect(")")
            return [None] * int(size or 0)
        return _Ref(name)

    def _object(self) -> dict:
        self._expect("{")
        result = {}
        if self._peek() == "}":
            self.pos += 1
            return result
        while True:
            ch = self._peek()
            if ch in "\"'":
                key = self._string()
            elif ch.isdigit():
                match = _NUMBER.match(self.src, self.pos)
                self.pos = match.end()
                key = match.group(0)
            else:
                key = self._ident()
            self._expect(":")
            result[key] = self.value()
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                if self._peek() == "}":
                    self.pos += 1
                    return result
                continue
            self._expect("}")
            return result

    def _array(self) -> list:
        self._expect("[")
        result = []
        if self._peek() == "]":
            self.pos += 1
            return result
        while True:
            result.append(self.value())
            ch = self._peek()
            if ch == ",":
                self.pos += 1
                if self._peek() == "]":
                    self.pos += 1
                    return result
                continue
            self._expect("]")
            return result

    def nuxt_expression(self) -> Any:
        """Either a plain object literal or the (function(params){return
        BODY}(args)) form."""
        if self._peek() == "{":
            return self.value()
        self._expect("(")
        self._expect("function")
        self._expect("(")
        params = []
        if self._peek() != ")":
            while True:
                params.append(self._ident())
                if self._peek() == ",":
                    self.pos += 1
                    continue
                break
        self._expect(")")
        self._expect("{")
        self._expect("return")
        body = self.value()
        if self._peek() == ";":
            self.pos += 1
        self._expect("}")
        self._expect("(")
        args = []
        if self._peek() != ")":
            while True:
                args.append(self.value())
                if self._peek() == ",":
                    self.pos += 1
                    continue
                break
        self._expect(")")
        env = {name: (args[i] if i < len(args) else None) for i, name in enumerate(params)}
        return _resolve(body, env)


def _resolve(node: Any, env: dict) -> Any:
    if isinstance(node, _Ref):
        return env.get(node.name)
    if isinstance(node, dict):
        return {k: _resolve(v, env) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, env) for v in node]
    return node


def extract_nuxt_state(html: str) -> Optional[dict]:
    """Returns the page's __NUXT__ object as plain Python data, or None
    when the page has none or it uses a construct this parser does not
    understand (logged by the caller as a skipped store, never fatal)."""
    start = html.find(_MARKER)
    if start < 0:
        return None
    end = html.find("</script>", start)
    source = html[start + len(_MARKER):end if end > 0 else len(html)].strip().rstrip(";")
    try:
        result = _Parser(source).nuxt_expression()
    except (NuxtParseError, IndexError, ValueError):
        return None
    return result if isinstance(result, dict) else None


_URL_KEYS = ("page_url", "external_url", "url", "link", "path")


def iter_urls(state: Any):
    """Yields every string stored under a URL like key anywhere in the
    state (navigation, footer, tiles, buttons): the crawler's link source,
    since the server HTML itself has almost no anchors."""
    if isinstance(state, dict):
        for key, value in state.items():
            if key in _URL_KEYS and isinstance(value, str) and value:
                yield value
            else:
                yield from iter_urls(value)
    elif isinstance(state, list):
        for item in state:
            yield from iter_urls(item)


if __name__ == "__main__":
    import sys
    import time

    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("usage: python nuxt_state.py saved_page.html")
        sys.exit(1)
    with open(path, encoding="utf-8") as handle:
        page = handle.read()
    started = time.perf_counter()
    parsed = extract_nuxt_state(page)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if parsed is None:
        print("No parsable __NUXT__ state found.")
    else:
        print(f"Top level keys: {list(parsed)}")
        print(f"State keys: {list((parsed.get('state') or {}))}")
        print(f"URLs found: {len(set(iter_urls(parsed)))}")
    print(f"Nuxt state parse latency: {elapsed_ms:.0f} ms")
